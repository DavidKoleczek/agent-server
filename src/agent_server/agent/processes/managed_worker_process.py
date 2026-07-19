"""Starts agent workers inside a platform process-tree boundary.

Every Windows worker owns a Job Object, allowing nested sub-agent jobs to be terminated independently.
On POSIX, the main worker starts a process group and sub-agents remain in that group so terminating the root reaches all descendants.
"""

from collections.abc import Sequence
from contextlib import suppress
import os
import signal
import subprocess
import sys
from typing import IO, TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING or sys.platform == "win32":
    from agent_server.agent.processes.windows_job import WindowsJob

_START_SIGNAL = b"agent-server-managed-process-start\n"


class _ProcessTree(Protocol):
    def terminate(self) -> None: ...

    def close(self) -> None: ...


class _PosixProcessTree:
    def __init__(self, process: subprocess.Popen[bytes], process_tree_root: bool) -> None:
        self._process = process
        self._process_tree_root = process_tree_root

    def terminate(self) -> None:
        if self._process_tree_root:
            with suppress(ProcessLookupError):
                _kill_posix_process_group(self._process.pid)
        elif self._process.poll() is None:
            self._process.kill()

    def close(self) -> None:
        return


class ManagedWorkerProcess:
    """Owns a subprocess and the operating-system container for its descendants."""

    def __init__(self, process: subprocess.Popen[bytes], process_tree: _ProcessTree) -> None:
        self.process = process
        self._process_tree = process_tree
        self._process_tree_closed = False
        self._closed = False

    @classmethod
    def start(
        cls,
        command: Sequence[str],
        *,
        process_tree_root: bool,
        startup_payload: bytes = b"",
    ) -> Self:
        """Starts a worker and releases it only after process-tree containment succeeds."""
        if sys.platform == "win32":
            process_tree = WindowsJob()
            try:
                process = _start_process(command, start_new_session=False)
            except Exception:
                process_tree.close()
                raise
            try:
                process_tree.assign(process.pid)
            except Exception:
                try:
                    _stop_process_after_failed_start(process)
                finally:
                    process_tree.close()
                raise
        else:
            process = _start_process(command, start_new_session=process_tree_root)
            process_tree = _PosixProcessTree(process, process_tree_root)

        managed_process = cls(process, process_tree)
        try:
            managed_process._release_startup_gate(startup_payload)
        except Exception:
            managed_process._clean_up_failed_start()
            raise
        return managed_process

    @property
    def stdin(self) -> IO[bytes] | None:
        return self.process.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self.process.stderr

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self) -> int:
        return self.process.wait()

    def terminate_tree(self) -> None:
        if self._process_tree_closed:
            return
        try:
            self._process_tree.terminate()
        except Exception:
            if self.poll() is None:
                self.process.kill()
            raise
        finally:
            self._process_tree.close()
            self._process_tree_closed = True

    def close(self) -> None:
        """Releases all process resources after the worker has exited."""
        if self._closed:
            return
        try:
            self.terminate_tree()
        finally:
            self._closed = True
            for pipe in (self.stdin, self.stdout, self.stderr):
                if pipe is not None:
                    pipe.close()

    def _release_startup_gate(self, startup_payload: bytes) -> None:
        stdin = self.stdin
        if stdin is None:
            raise RuntimeError("Managed process stdin is unavailable.")
        stdin.write(_START_SIGNAL)
        if startup_payload:
            stdin.write(startup_payload)
        stdin.flush()

    def _clean_up_failed_start(self) -> None:
        try:
            _stop_process_after_failed_start(self.process)
        finally:
            self.close()


def wait_for_start_signal() -> bool:
    """Waits until the parent has placed this worker in its process container."""
    line = sys.stdin.buffer.readline()
    if not line:
        return False
    if line != _START_SIGNAL:
        raise RuntimeError("Invalid managed process start signal.")
    return True


def _kill_posix_process_group(process_group_id: int) -> None:
    kill_process_group = vars(os).get("killpg")
    kill_signal = vars(signal).get("SIGKILL")
    if not callable(kill_process_group) or not isinstance(kill_signal, int):
        raise RuntimeError("POSIX process-group termination is unavailable.")
    kill_process_group(process_group_id, kill_signal)


def _start_process(command: Sequence[str], *, start_new_session: bool) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=start_new_session,
    )


def _stop_process_after_failed_start(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()
