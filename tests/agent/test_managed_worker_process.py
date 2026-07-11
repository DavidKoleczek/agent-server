import asyncio
import ctypes
from ctypes import wintypes
import json
import os
import sys
from textwrap import dedent
import time

import pytest

from agent_server.agent.processes.managed_worker_process import ManagedWorkerProcess

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_PROCESS_EXIT_TIMEOUT_SECONDS = 5.0

_WORKER_WITH_CHILD = dedent(
    """
    import json
    import os
    import subprocess
    import sys
    import time

    from agent_server.agent.processes.managed_worker_process import wait_for_start_signal

    if not wait_for_start_signal():
        raise SystemExit
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(json.dumps({"worker": os.getpid(), "child": child.pid}), flush=True)
    time.sleep(60)
    """
)

_NESTED_WORKER = dedent(
    """
    import json
    import subprocess
    import sys
    import time

    from agent_server.agent.processes.managed_worker_process import wait_for_start_signal

    if not wait_for_start_signal():
        raise SystemExit
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(json.dumps({"child": child.pid}), flush=True)
    time.sleep(60)
    """
)

_ROOT_WITH_NESTED_WORKER = dedent(
    """
    import json
    import os
    import sys
    import time

    from agent_server.agent.processes.managed_worker_process import ManagedWorkerProcess, wait_for_start_signal

    if not wait_for_start_signal():
        raise SystemExit
    nested = ManagedWorkerProcess.start(
        [sys.executable, "-c", sys.argv[1]],
        process_tree_root=False,
    )
    nested_output = nested.stdout.readline()
    print(
        json.dumps(
            {
                "root": os.getpid(),
                "nested": nested.process.pid,
                **json.loads(nested_output),
            }
        ),
        flush=True,
    )
    if sys.stdin.buffer.readline() == b"terminate-nested\\n":
        nested.terminate_tree()
        nested.wait()
        nested.close()
        print("nested-terminated", flush=True)
    time.sleep(60)
    """
)


async def test_terminate_tree_stops_worker_and_child() -> None:
    worker = ManagedWorkerProcess.start(
        [sys.executable, "-c", _WORKER_WITH_CHILD],
        process_tree_root=True,
    )
    try:
        process_ids = await _read_process_ids(worker)
        assert all(_is_process_running(process_id) for process_id in process_ids.values())

        worker.terminate_tree()
        await asyncio.to_thread(worker.wait)

        for process_id in process_ids.values():
            await _wait_until_process_stops(process_id)
    finally:
        await _close_worker(worker)


@pytest.mark.skipif(sys.platform != "win32", reason="Nested Job Objects are Windows-specific.")
async def test_nested_job_termination_does_not_stop_parent() -> None:
    worker = ManagedWorkerProcess.start(
        [sys.executable, "-c", _ROOT_WITH_NESTED_WORKER, _NESTED_WORKER],
        process_tree_root=True,
    )
    try:
        process_ids = await _read_process_ids(worker)
        assert all(_is_process_running(process_id) for process_id in process_ids.values())

        stdin = worker.stdin
        stdout = worker.stdout
        assert stdin is not None
        assert stdout is not None
        stdin.write(b"terminate-nested\n")
        stdin.flush()
        confirmation = await asyncio.wait_for(asyncio.to_thread(stdout.readline), timeout=5.0)
        assert confirmation.strip() == b"nested-terminated"

        await _wait_until_process_stops(process_ids["nested"])
        await _wait_until_process_stops(process_ids["child"])
        assert _is_process_running(process_ids["root"])
    finally:
        await _close_worker(worker)


async def _read_process_ids(worker: ManagedWorkerProcess) -> dict[str, int]:
    stdout = worker.stdout
    assert stdout is not None
    line = await asyncio.wait_for(asyncio.to_thread(stdout.readline), timeout=5.0)
    payload = json.loads(line)
    assert isinstance(payload, dict)
    return {str(name): int(process_id) for name, process_id in payload.items()}


async def _close_worker(worker: ManagedWorkerProcess) -> None:
    try:
        worker.terminate_tree()
    finally:
        try:
            await asyncio.to_thread(worker.wait)
        finally:
            worker.close()


async def _wait_until_process_stops(process_id: int) -> None:
    deadline = time.monotonic() + _PROCESS_EXIT_TIMEOUT_SECONDS
    while _is_process_running(process_id):
        if time.monotonic() >= deadline:
            pytest.fail(f"Process did not stop: {process_id}")
        await asyncio.sleep(0.05)


def _is_process_running(process_id: int) -> bool:
    if sys.platform == "win32":
        return _is_windows_process_running(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True


def _is_windows_process_running(process_id: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = ctypes.WINFUNCTYPE(
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        use_last_error=True,
    )(("OpenProcess", kernel32))
    get_exit_code_process = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        use_last_error=True,
    )(("GetExitCodeProcess", kernel32))
    close_handle = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        use_last_error=True,
    )(("CloseHandle", kernel32))

    handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return exit_code.value == _STILL_ACTIVE
    finally:
        close_handle(handle)
