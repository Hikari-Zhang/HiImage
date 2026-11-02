"""CLI 截止时间回归：以短时 Python 子进程替代 IOPaint，不加载模型。"""
import base64
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import cv2
import numpy as np
import pytest

from core import inpainter


@pytest.fixture
def cli(monkeypatch, tmp_path):
    processes = []
    original_popen = subprocess.Popen
    output = tmp_path / "output"
    output.mkdir()
    image = np.full((3, 4, 3), (12, 34, 56), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    assert ok
    save_result = (
        "import base64, pathlib; "
        f"pathlib.Path({str(output / 'image.png')!r}).write_bytes(base64.b64decode({base64.b64encode(encoded).decode()!r}))"
    )

    class CLI:
        script = ""
        launch_error = None
        def __init__(self):
            self.model = inpainter.Inpainter.__new__(inpainter.Inpainter)
            self.model.iopaint_path = "fake-iopaint"
            self.model._iopaint_model_id = "lama"
            self.model.device = "cpu"
            self.model.progress_callback = None
            self.model._timeout = 0.2
            self.processes = processes
            self.image = image
            self.save_result = save_result
            self.output = output
            self.log_paths = []

        def run(self):
            return self.model._run_iopaint_cli(str(tmp_path / "input.png"), str(tmp_path / "mask.png"), str(output))

    state = CLI()
    def launch(command, **kwargs):
        if command[0] != "fake-iopaint":
            return original_popen(command, **kwargs)
        if kwargs.get("stdout") is not subprocess.PIPE:
            state.log_paths.append(Path(kwargs["stdout"].name))
        if state.launch_error:
            raise state.launch_error
        process = original_popen([sys.executable, "-u", "-c", state.script], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(inpainter.subprocess, "Popen", launch)
    yield state
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        if process.stdout is not None:
            process.stdout.close()


@pytest.mark.parametrize("noisy", [False, True], ids=["silent", "continuous-log"])
def test_total_deadline_applies_even_while_reading_child_output(cli, noisy):
    if noisy:
        cli.script = "import time\nfor i in range(20):\n print('25% processing', flush=True)\n time.sleep(0.05)\n" + cli.save_result
    else:
        cli.script = "import time; time.sleep(1); " + cli.save_result
    started = time.monotonic()
    with pytest.raises(Exception, match="超时"):
        cli.run()
    assert time.monotonic() - started < 0.9
    assert all(process.poll() is not None for process in cli.processes)
    assert all(not path.exists() for path in cli.log_paths)


def test_success_keeps_rgb_output_and_streams_unicode_cr_lf_progress(cli, capsys):
    progress = []
    cli.model._timeout = 2
    cli.model.progress_callback = lambda percent, _: progress.append((percent, cli.processes[0].poll()))
    text = "加载模型\r0% ready\r\n25% progress\n25% duplicate\r50% progress\r"
    cli.script = f"import os,time; os.write(1, {text.encode('utf-8')!r}); time.sleep(0.15); " + cli.save_result
    result = cli.run()
    assert np.array_equal(result, cli.image)
    assert [percent for percent, _ in progress] == [0, 25, 50, 100]
    assert progress[1][1] is None  # 子进程尚未结束时就能收到不足 256 字符的进度。
    assert "加载模型" in capsys.readouterr().out
    assert all(not path.exists() for path in cli.log_paths)


def test_split_utf8_and_crlf_are_decoded_without_garbled_output(cli, capsys):
    cli.model._timeout = 2
    raw = "中文日志\r\n".encode("utf-8")
    cli.script = f"import os,time; os.write(1,{raw[:1]!r}); time.sleep(0.06); os.write(1,{raw[1:-1]!r}); time.sleep(0.06); os.write(1,{raw[-1:]!r}); " + cli.save_result
    cli.run()
    output = capsys.readouterr().out
    assert "中文日志" in output
    assert "\ufffd" not in output


def test_start_failure_cleans_log_files(cli):
    cli.launch_error = FileNotFoundError("fake executable missing")
    with pytest.raises(RuntimeError, match="启动失败"):
        cli.run()
    assert not cli.processes
    assert all(not path.exists() for path in cli.log_paths)


def test_nonzero_exit_keeps_error_diagnostics(cli):
    cli.model._timeout = 2
    cli.script = "import sys; print('native failure detail',flush=True); sys.exit(7)"
    with pytest.raises(Exception, match="返回码 7") as error:
        cli.run()
    assert "native failure detail" in str(error.value)
    assert all(process.poll() is not None for process in cli.processes)
    assert all(not path.exists() for path in cli.log_paths)


@pytest.mark.parametrize("invalid", [False, True], ids=["missing", "invalid-image"])
def test_successful_exit_still_validates_output_image(cli, invalid):
    cli.model._timeout = 2
    cli.script = f"from pathlib import Path; Path({str(cli.output / 'image.png')!r}).write_bytes(b'bad image')" if invalid else "print('finished')"
    with pytest.raises(Exception, match="无法读取处理结果" if invalid else "输出文件不存在"):
        cli.run()
    assert all(not path.exists() for path in cli.log_paths)


def test_callback_exception_stops_process_and_keeps_original_error(cli):
    def callback(*_):
        raise RuntimeError("callback failed")
    cli.model._timeout = 2
    cli.model.progress_callback = callback
    cli.script = "import os,time; os.write(1,b'25% ready\\r'); time.sleep(1)"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="callback failed"):
        cli.run()
    assert time.monotonic() - started < 0.9
    assert all(process.poll() is not None for process in cli.processes)
    assert all(not path.exists() for path in cli.log_paths)


def test_timeout_releases_inference_slot_for_next_task(cli):
    from app.shared_executor import InferenceScheduler
    cli.script = "import time; time.sleep(1)"

    async def scenario():
        scheduler = InferenceScheduler(max_pending=1)
        async def first():
            async with scheduler.admission("timed-out CLI"):
                with pytest.raises(TimeoutError, match="超时"):
                    await scheduler.run_blocking(cli.run)
        async def second():
            async with scheduler.admission("next task"):
                return await scheduler.run_blocking(lambda: "next task succeeded")
        try:
            result = await asyncio.gather(first(), second())
            assert result[1] == "next task succeeded"
            assert scheduler.snapshot()["running"] == 0
            assert scheduler.snapshot()["pending"] == 0
            assert all(process.poll() is not None for process in cli.processes)
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows Job 原生进程树验证")
def test_windows_timeout_reclaims_launcher_descendants(cli):
    import ctypes
    from ctypes import wintypes
    cli.model._timeout = 0.5
    child_pid_file = cli.output / "child.pid"
    cli.script = (
        "import subprocess,sys,time,pathlib; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(3)"
    )
    with pytest.raises(TimeoutError, match="超时"):
        cli.run()
    child_pid = int(child_pid_file.read_text())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    handle = kernel.OpenProcess(0x1001, False, child_pid)
    if handle:
        code = wintypes.DWORD()
        try:
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            assert code.value != 259  # STILL_ACTIVE
        finally:
            if code.value == 259:
                kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
    assert all(not path.exists() for path in cli.log_paths)
