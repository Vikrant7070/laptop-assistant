"""A Windows "job" around the brain's process.

Everything the brain starts stays inside the job. Ending the job ends all of it at once, and if the
guard itself dies, Windows ends the job too (kill-on-close), so a brain can never outlive its guard.
Uses only documented kernel32 calls. On other systems it falls back to killing the one process.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class Job:
    def __init__(self, proc: subprocess.Popen):
        self.proc, self.handle = proc, None
        if sys.platform != "win32":
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k32 = k32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        if ok and k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))):
            self.handle = job
        else:
            k32.CloseHandle(job)

    @property
    def contained(self) -> bool:
        return self.handle is not None

    def kill(self) -> None:
        if self.handle is not None:
            self.k32.TerminateJobObject(self.handle, 1)
        if self.proc.poll() is None:
            self.proc.kill()

    def close(self) -> None:
        if self.handle is not None:
            self.k32.CloseHandle(self.handle)  # kill-on-close: anything left in the job ends here
            self.handle = None
