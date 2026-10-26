"""Read a real PTY into a terminal grid; no application imports or test hooks."""

from __future__ import annotations

import codecs
import os
from pathlib import Path
import signal
import struct
import threading
import time

import pyte
from wcwidth import wcswidth

_INPUT_LEFT_BORDER = "▊"
_INPUT_RIGHT_BORDER = "▎"


class TerminalSession:
    def __init__(self, command, cwd: Path, env, evidence: Path, *, dimensions=(45, 160)):
        rows, columns = dimensions
        self.evidence = evidence
        evidence.mkdir(parents=True, exist_ok=True)
        self.screen = pyte.Screen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.condition = threading.Condition()
        self.finished = threading.Event()
        self.reader_error = None
        self.exit_code = None
        if os.name == "nt":
            from winpty import PtyProcess

            self.process = PtyProcess.spawn(command, cwd=str(cwd), env=env, dimensions=(rows, columns))
        else:
            import fcntl
            import pty
            import termios

            self.pid, self.master = pty.fork()
            if self.pid == 0:
                os.chdir(cwd)
                os.execvpe(command[0], command, env)
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            with (self.evidence / "terminal.ansi").open("w", encoding="utf-8") as log:
                while True:
                    if os.name == "nt":
                        chunk = self.process.read(65536)
                    else:
                        chunk = decoder.decode(os.read(self.master, 65536))
                        if not chunk:
                            break
                    log.write(chunk)
                    log.flush()
                    with self.condition:
                        self.stream.feed(chunk)
                        self.condition.notify_all()
        except EOFError:
            pass
        except OSError as exc:
            # Linux PTYs signal normal slave closure as EIO.
            if os.name == "nt" or exc.errno != 5:
                self.reader_error = exc
        finally:
            self.finished.set()
            with self.condition:
                self.condition.notify_all()

    def text(self):
        with self.condition:
            return "\n".join(self._render_row(row) for row in range(self.screen.lines))

    def _render_row(self, row):
        # ConPTY can erase half of a wide glyph, leaving an empty cell in pyte.
        # Preserve its column instead of using Screen.display, which indexes data[0].
        rendered, previous_width = [], 1
        for column in range(self.screen.columns):
            data = self.screen.buffer[row][column].data
            if data:
                rendered.append(data)
                previous_width = wcswidth(data)
            else:
                if previous_width != 2:
                    rendered.append(" ")
                previous_width = 1
        return "".join(rendered)

    def find(self, text, *, max_column=None):
        with self.condition:
            for row in range(self.screen.lines):
                characters, columns = [], []
                for column in range(min(max_column or self.screen.columns, self.screen.columns)):
                    for character in self.screen.buffer[row][column].data:
                        characters.append(character)
                        columns.append(column)
                index = "".join(characters).find(text)
                if index >= 0:
                    return (columns[index] + columns[index + len(text) - 1]) // 2, row
        return None

    def wait(self, predicate, description, *, timeout=30):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not predicate():
                if self.reader_error is not None:
                    raise RuntimeError(f"Terminal reader failed: {self.reader_error}")
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.finished.is_set():
                    self.capture("failure")
                    raise AssertionError(f"{description}\n{self.text()}")
                self.condition.wait(remaining)

    def wait_text(self, text):
        self.wait(lambda: self.find(text) is not None, f"Terminal did not show {text!r}")

    def send(self, text):
        if os.name == "nt":
            self.process.write(text)
        else:
            os.write(self.master, text.encode("utf-8"))

    def paste(self, text):
        self.send("\x01\x1b[200~" + text + "\x1b[201~")

    def click(self, label):
        self.wait_text(label)
        column, row = self.find(label)
        self.click_at(column, row)

    def click_at(self, column, row):
        self.send(f"\x1b[<0;{column + 1};{row + 1}M\x1b[<0;{column + 1};{row + 1}m")

    def wait_focused_button(self, label):
        def focused():
            point = self.find(label)
            if point is None:
                return False
            column, row = point
            return any(
                (cell := self.screen.buffer[row][index]).data == "▊" and cell.fg != cell.bg
                for index in range(column)
            )
        self.wait(focused, f"Button did not finish receiving focus: {label}")

    def fill_input(self, visible_text, value, *, max_column=None):
        self.wait(lambda: self.find(visible_text, max_column=max_column) is not None, f"Input not visible: {visible_text}")
        column, row = self.find(visible_text, max_column=max_column)
        self.fill_input_at(column, row, value)

    def fill_source_input(self, value):
        # The wide test layout wraps the source label beside its input's text row.
        self.wait(
            lambda: (point := self.find("palette")) is not None
            and point[1] == self.screen.lines - 1
            and self.find("本次分析範圍") is not None,
            "Initial terminal frame and source scope are not ready",
        )
        self.wait_text("來源（檔案")
        column, row = self.find("來源（檔案")
        self.fill_input_at(column + 20, row, value)

    def fill_input_at(self, column, row, value):
        left, _ = self._input_bounds_at(column, row)
        self.click_at(column, row)
        self.paste(value)
        # Reflow can move the row and shrink its right edge when a scrollbar appears.
        # Restrict matching to bordered inputs in this form column, never summary text.
        visible_parts = tuple(part for part in (value[:20], value[-20:]) if part)

        def value_visible_in_input():
            for current_row in range(self.screen.lines):
                cells = self.screen.buffer[current_row]
                if cells[left].data != _INPUT_LEFT_BORDER:
                    continue
                right = next((index for index in range(left + 1, self.screen.columns)
                              if cells[index].data == _INPUT_RIGHT_BORDER), None)
                if right is None:
                    continue
                content = "".join(cells[index].data for index in range(left + 1, right))
                if any(part in content for part in visible_parts):
                    return True
            return False

        self.wait(
            value_visible_in_input,
            f"Input in form column {left} did not display its new value",
        )

    def _input_bounds_at(self, column, row):
        cells = self.screen.buffer[row]
        left = next(
            (index for index in range(column - 1, -1, -1)
             if cells[index].data == _INPUT_LEFT_BORDER),
            None,
        )
        right = next(
            (index for index in range(column + 1, self.screen.columns)
             if cells[index].data == _INPUT_RIGHT_BORDER),
            None,
        )
        if left is None or right is None:
            raise AssertionError(f"Target at ({column}, {row}) has no visible Input borders")
        return left, right

    def resize(self, columns, rows):
        with self.condition:
            self.screen.resize(lines=rows, columns=columns)
            if os.name == "nt":
                self.process.setwinsize(rows, columns)
            else:
                import fcntl
                import termios

                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
                os.kill(self.pid, signal.SIGWINCH)

    def capture(self, name):
        (self.evidence / f"{name}.txt").write_text(self.text(), encoding="utf-8")

    def wait_exit(self, *, timeout=15):
        if not self.finished.wait(timeout):
            self.capture("exit-timeout")
            raise AssertionError("Terminal application did not exit")
        if os.name == "nt":
            self.exit_code = self.process.wait()
        else:
            _, status = os.waitpid(self.pid, 0)
            self.exit_code = os.waitstatus_to_exitcode(status)
        if self.exit_code != 0:
            raise AssertionError(f"Terminal application exited with {self.exit_code}: {self.text()}")

    def close(self):
        if os.name == "nt":
            self.process.close(force=not self.finished.is_set())
        else:
            if not self.finished.is_set():
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, 0)
            os.close(self.master)
        self.reader.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
