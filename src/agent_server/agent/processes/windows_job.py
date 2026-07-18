"""Windows Job Object containment for managed process trees.

`Popen.kill()` calls `TerminateProcess`, which only terminates the direct process. A Job Object groups that process,
its descendants, and nested jobs so `TerminateJobObject` can terminate the complete tree.

Sources:
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject
"""

import ctypes
from ctypes import wintypes
from typing import Never

# `ctypes` does not import the constants from WinNT.h. Information class 9 tells `SetInformationJobObject` to
# interpret the supplied buffer as `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`; another class expects a different layout.
# Source: https://learn.microsoft.com/en-us/windows/win32/api/winnt/ne-winnt-jobobjectinfoclass
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# Explicit termination handles cancellation and shutdown. This limit is also required as a fail-safe: without it,
# closing the last job handle does not terminate remaining processes if their manager exits before explicit cleanup.
# Source: https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

# `AssignProcessToJobObject` requires a process handle with both of these access rights.
# Sources:
# https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
# https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

# `TerminateJobObject` applies a caller-selected exit code to every terminated process.
# A nonzero value distinguishes forced termination at the OS level
_FORCED_TERMINATION_EXIT_CODE = 1


# These definitions mirror the documented WinNT.h field order and native types. `ctypes` supplies native alignment,
# and `ctypes.sizeof()` passes the resulting ABI-sized buffer to `SetInformationJobObject`.
# Sources:
# https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
# https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information
class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# These prototypes mirror the Windows SDK signatures. Declaring them is necessary for `ctypes` to marshal handles,
# pointers, and integer widths correctly and to retain the thread-local error reported by `GetLastError`.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_create_job_object = ctypes.WINFUNCTYPE(
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCWSTR,
    use_last_error=True,
)(("CreateJobObjectW", _kernel32))
_set_information_job_object = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.INT,
    wintypes.LPVOID,
    wintypes.DWORD,
    use_last_error=True,
)(("SetInformationJobObject", _kernel32))
_open_process = ctypes.WINFUNCTYPE(
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
    use_last_error=True,
)(("OpenProcess", _kernel32))
_assign_process_to_job_object = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.HANDLE,
    use_last_error=True,
)(("AssignProcessToJobObject", _kernel32))
_terminate_job_object = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.UINT,
    use_last_error=True,
)(("TerminateJobObject", _kernel32))
_close_handle = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    use_last_error=True,
)(("CloseHandle", _kernel32))


class WindowsJob:
    """Owns a Windows Job Object containing one process tree."""

    def __init__(self) -> None:
        handle = _create_job_object(None, None)
        if not handle:
            _raise_last_error("CreateJobObjectW")
        self._handle: int | None = handle

        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _set_information_job_object(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            _close_handle(handle)
            self._handle = None
            _raise_windows_error("SetInformationJobObject", error)

    def assign(self, process_id: int) -> None:
        """Assigns a process to this job before it is released to perform work."""
        handle = self._require_handle()
        process_handle = _open_process(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
            False,
            process_id,
        )
        if not process_handle:
            _raise_last_error("OpenProcess")

        assigned = _assign_process_to_job_object(handle, process_handle)
        assignment_error = ctypes.get_last_error()
        closed = _close_handle(process_handle)
        close_error = ctypes.get_last_error()

        if not assigned:
            _raise_windows_error("AssignProcessToJobObject", assignment_error)
        if not closed:
            _raise_windows_error("CloseHandle", close_error)

    def terminate(self) -> None:
        """Terminates every process in this job and any nested jobs."""
        handle = self._handle
        if handle is None:
            return
        if not _terminate_job_object(handle, _FORCED_TERMINATION_EXIT_CODE):
            _raise_last_error("TerminateJobObject")

    def close(self) -> None:
        """Closes the job handle, which also terminates any remaining processes."""
        handle = self._handle
        if handle is None:
            return
        if not _close_handle(handle):
            _raise_last_error("CloseHandle")
        self._handle = None

    def _require_handle(self) -> int:
        if self._handle is None:
            raise RuntimeError("Windows Job Object is closed.")
        return self._handle


def _raise_last_error(operation: str) -> Never:
    _raise_windows_error(operation, ctypes.get_last_error())


def _raise_windows_error(operation: str, error_code: int) -> Never:
    message = ctypes.FormatError(error_code).strip()
    raise OSError(error_code, f"{operation} failed: {message}")
