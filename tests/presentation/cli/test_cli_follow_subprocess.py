"""以真實程序與中斷訊號驗證 CLI 追蹤生命週期。"""

import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import sys
import threading
from time import monotonic


def _read_lines(stream, messages, collected):
    for line in stream:
        collected.append(line)
        messages.put(line)


def _wait_for_text(messages, expected, process, timeout=10):
    deadline = monotonic() + timeout
    observed = []
    while True:
        remaining = deadline - monotonic()
        assert remaining > 0, f"未看到 {expected!r}; exit={process.poll()}, lines={observed}"
        try:
            line = messages.get(timeout=remaining)
        except Empty as error:
            raise AssertionError(f"未看到 {expected!r}; exit={process.poll()}, lines={observed}") from error
        observed.append(line)
        if expected in line:
            return


def _interrupt_owned_process(process):
    if os.name != "nt":
        process.send_signal(signal.SIGINT)
        return
    # 子程序有專屬的隱藏 console；helper 只附加該 PID，不影響使用者終端機。
    helper = """
import ctypes
import sys
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.FreeConsole()
if not kernel.AttachConsole(int(sys.argv[1])):
    raise ctypes.WinError(ctypes.get_last_error())
try:
    if not kernel.SetConsoleCtrlHandler(None, True):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        raise ctypes.WinError(ctypes.get_last_error())
finally:
    kernel.FreeConsole()
"""
    subprocess.run(
        [sys.executable, "-c", helper, str(process.pid)], check=True,
        timeout=10, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def test_follow_native_ctrl_c_flushes_read_tail_once_and_exits_130(tmp_path):
    """真實 Ctrl+C 結束已讀尾事件，stdout 無重複、stderr 無 traceback。"""
    source = tmp_path / "source.log"

    def line(message):
        return f"2026-10-31 12:00:00.000 [main] ERROR sample.App - {message}\n"

    source.write_text(line("first-complete") + line("second-pending"), encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[3] / "src")
    startup = {}
    if os.name == "nt":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        startup = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": info}
    with subprocess.Popen(
        [sys.executable, "-m", "log_analyzer", str(source), "--follow"],
        cwd=tmp_path, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", **startup,
    ) as process:
        stdout_queue, stderr_queue = Queue(), Queue()
        stdout_lines, stderr_lines = [], []
        threads = [
            threading.Thread(target=_read_lines, args=(process.stdout, stdout_queue, stdout_lines)),
            threading.Thread(target=_read_lines, args=(process.stderr, stderr_queue, stderr_lines)),
        ]
        for thread in threads:
            thread.start()
        try:
            _wait_for_text(stdout_queue, "first-complete", process)
            _wait_for_text(stderr_queue, "尚未完成", process)
            with source.open("a", encoding="utf-8") as stream:
                stream.write(line("third-tail"))
            _wait_for_text(stdout_queue, "second-pending", process)
            _interrupt_owned_process(process)
            assert process.wait(timeout=10) == 130
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for thread in threads:
                thread.join(timeout=10)
            assert all(not thread.is_alive() for thread in threads)

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    assert stdout == line("first-complete") + line("second-pending") + line("third-tail")
    assert "Traceback" not in stderr
    assert "追蹤已停止" in stderr
    assert not list(tmp_path.glob("*.csv"))
