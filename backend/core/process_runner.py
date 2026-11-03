"""进程硬截止时间与进程树回收；供后续独立推理 worker 使用。"""
import subprocess
import os
import sys
import tempfile
import codecs
import logging
import signal
import time
from contextlib import contextmanager
from pathlib import Path


class ProcessCleanupError(RuntimeError):
    """未能确认进程树已退出。"""


class ProcessCancelled(RuntimeError):
    """调用方取消了子进程。"""


def _stop_cli_process(proc: subprocess.Popen, *, confirm_group=False) -> None:
    """回收超时/异常的 CLI，优先终止其进程树，再等待直接子进程退出。"""
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    deadline = time.monotonic() + 5
    proc.wait(timeout=max(0, deadline - time.monotonic()))
    if confirm_group and os.name != "nt":
        while True:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                raise ProcessCleanupError("无法确认推理进程组退出")
            time.sleep(0.01)


@contextmanager
def _windows_cli_job(proc):
    """用独立 Windows Job 回收 CLI 的后代进程，不扫描或终止宿主进程。"""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
            ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
        ]

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("times", ctypes.c_longlong * 4), ("page_faults", wintypes.DWORD),
            ("total_processes", wintypes.DWORD), ("active_processes", wintypes.DWORD),
            ("terminated_processes", wintypes.DWORD),
        ]

    class ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
            ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = kernel.Thread32First.argtypes
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        # 进程以 CREATE_SUSPENDED 创建，先入 Job 再启动，避免 launcher 的子进程逃逸。
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found and entry.process_id != proc.pid:
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if not found:
                raise RuntimeError("无法定位 子进程 启动线程")
            thread = kernel.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(thread)
        finally:
            kernel.CloseHandle(snapshot)
        yield
    finally:
        try:
            if not kernel.TerminateJobObject(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # Job 关闭/终止是异步的；等待全部进程退出后才清理日志文件。
            deadline = time.monotonic() + 5
            accounting = Accounting()
            while True:
                if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.active_processes == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("推理 子进程组回收超时")
                time.sleep(0.01)
        except (OSError, TimeoutError) as exc:
            raise ProcessCleanupError("无法确认 Windows 推理进程树退出") from exc
        finally:
            kernel.CloseHandle(job)


def _stream_cli_output(cmd, env, timeout, consume, *, cwd=None, cancelled=None,
                       confirm_group=False, deadline=None) -> int:
    """日志走独立文件句柄，读取已有数据不会等待子进程输出或关闭管道。"""
    if deadline is None:
        deadline = time.monotonic() + timeout
    with _cli_log_directory() as log_dir:
        log_path = Path(log_dir) / "stdout.log"
        # 分别 open，不能 dup 同一个句柄，否则读取 seek 会改变子进程写入位置。
        with log_path.open("wb") as writer, log_path.open("rb") as reader:
            try:
                proc = subprocess.Popen(
                    cmd, stdout=writer, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                    start_new_session=os.name != "nt",
                    creationflags=0x4 if os.name == "nt" else 0,  # CREATE_SUSPENDED
                )
            except OSError as exc:
                raise RuntimeError(f"子进程启动失败: {exc}") from exc
            try:
                with _windows_cli_job(proc):
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    while True:
                        if cancelled is not None and cancelled():
                            raise ProcessCancelled("推理已取消")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(cmd, timeout)
                        exited = proc.poll() is not None
                        chunk = reader.read(8192)
                        if chunk:
                            consume(decoder.decode(chunk))
                            continue
                        if exited:
                            break
                        try:
                            proc.wait(timeout=min(0.05, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                    consume(decoder.decode(b"", final=True))
                    return proc.returncode
            finally:
                if os.name != "nt" or proc.poll() is None:
                    try:
                        _stop_cli_process(proc, confirm_group=confirm_group)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        raise ProcessCleanupError("无法确认推理子进程退出") from exc


@contextmanager
def _cli_log_directory():
    directory = tempfile.TemporaryDirectory(prefix="hiimage_cli_log_")
    try:
        yield directory.name
    finally:
        primary_error = sys.exc_info()[1]
        deadline = time.monotonic() + 0.5
        while True:
            try:
                directory.cleanup()
                break
            except PermissionError:
                # Windows 进程已结束后，文件句柄释放仍可能短暂滞后。
                if time.monotonic() >= deadline:
                    if primary_error is not None:
                        logging.getLogger(__name__).exception("CLI 日志清理失败: %s", directory.name)
                        break
                    raise
                time.sleep(0.01)
