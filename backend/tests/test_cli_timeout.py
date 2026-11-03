"""真实短时子进程验证硬截止时间与回收，无需模型或网络。"""
import asyncio
import os
import subprocess
import sys
import time
import pytest
from core.process_runner import _stream_cli_output


def run(script, timeout=0.2, callback=None):
    chunks = []
    code = _stream_cli_output([sys.executable, "-u", "-c", script], {**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout,
                              callback or chunks.append)
    return code, "".join(chunks).replace("\r\n", "\n")


@pytest.mark.parametrize("script", ["import time;time.sleep(3)",
    "import time\nwhile True:\n print('working',flush=True)\n time.sleep(.01)"])
def test_deadline_stops_silent_and_noisy_process(script):
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run(script)
    assert time.monotonic() - started < 1.5


def test_success_unicode_and_nonzero_diagnostics():
    assert run("print('中文日志')", 3) == (0, "中文日志\n")
    assert run("import sys;print('failure');sys.exit(7)", 3) == (7, "failure\n")


def test_callback_error_keeps_original_exception():
    def fail(_): raise RuntimeError("callback failed")
    with pytest.raises(RuntimeError, match="callback failed"):
        run("import time;print('ready',flush=True);time.sleep(3)", 3, fail)


def test_missing_executable():
    with pytest.raises(RuntimeError, match="启动失败"):
        _stream_cli_output(["missing-hiimage-test-command"], os.environ.copy(), 1, lambda _:None)


def test_timeout_releases_scheduler_slot():
    from app.shared_executor import InferenceScheduler
    async def scenario():
        scheduler = InferenceScheduler(max_pending=1)
        async def first():
            async with scheduler.admission("deadline"):
                with pytest.raises(subprocess.TimeoutExpired):
                    await scheduler.run_blocking(run, "import time;time.sleep(3)")
        async def second():
            async with scheduler.admission("next"):
                return await scheduler.run_blocking(lambda:"ok")
        try:
            assert (await asyncio.gather(first(),second()))[1] == "ok"
            assert scheduler.snapshot()["running"] == 0
        finally: await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows Job 进程树回收")
def test_windows_timeout_reclaims_descendants(tmp_path):
    import ctypes
    from ctypes import wintypes
    marker = tmp_path / "child.pid"
    script = ("import subprocess,sys,time,pathlib; "
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']); "
              f"pathlib.Path({str(marker)!r}).write_text(str(child.pid));time.sleep(3)")
    with pytest.raises(subprocess.TimeoutExpired): run(script, .8)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,int(marker.read_text()))
    if handle:
        try:
            code=wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle,ctypes.byref(code))
            assert code.value != 259
        finally: kernel.CloseHandle(handle)
