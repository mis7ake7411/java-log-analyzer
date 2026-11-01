"""將同步更新框架完整送入終端模擬器。"""

from __future__ import annotations

from collections.abc import Callable

_QUERY = "\x1b[?2026$p"
_REPLY = "\x1b[?2026;2$y"
_SYNC_START = "\x1b[?2026h"
_SYNC_END = "\x1b[?2026l"
_CONTROL_SEQUENCES = (_QUERY, _SYNC_START, _SYNC_END)


class IncompleteSynchronizedFrame(RuntimeError):
    """PTY 結束時仍未收到同步更新框架的結束標記。"""


class TerminalFrameStream:
    """辨識終端同步輸出控制序列並延後發布不完整畫面。

    Args:
        feed: 將可顯示的文字送入終端模擬器。
        send: 將終端查詢的回覆送回 PTY。
        publish: 模擬器畫面更新後通知等待中的讀取者。
    """

    def __init__(
        self,
        *,
        feed: Callable[[str], None],
        send: Callable[[str], None],
        publish: Callable[[], None],
    ) -> None:
        self._feed = feed
        self._send = send
        self._publish = publish
        self._pending = ""
        self._frame: list[str] | None = None

    def feed(self, chunk: str) -> None:
        """處理任意邊界的 PTY 輸出片段。

        Args:
            chunk: 從 PTY 讀到的文字片段。

        Returns:
            None。
        """
        if not chunk:
            return

        data = self._pending + chunk
        self._pending = ""
        visible: list[str] = []
        index = 0
        while index < len(data):
            sequence = next(
                (item for item in _CONTROL_SEQUENCES if data.startswith(item, index)),
                None,
            )
            if sequence is not None:
                if sequence == _QUERY:
                    self._publish_visible(visible)
                    visible.clear()
                    self._send(_REPLY)
                elif sequence == _SYNC_START:
                    if self._frame is None:
                        self._publish_visible(visible)
                        visible.clear()
                        self._frame = []
                elif self._frame is None:
                    # 未配對的結束標記仍交給既有終端解析器處理。
                    visible.append(sequence)
                else:
                    frame = "".join(self._frame)
                    self._frame = None
                    if frame:
                        self._feed(frame)
                    self._publish()
                index += len(sequence)
                continue

            suffix = data[index:]
            if any(item.startswith(suffix) for item in _CONTROL_SEQUENCES):
                self._pending = suffix
                break

            character = data[index]
            if self._frame is None:
                visible.append(character)
            else:
                self._frame.append(character)
            index += 1

        self._publish_visible(visible)

    def finish(self) -> None:
        """結束串流並診斷未完成框架。

        Raises:
            IncompleteSynchronizedFrame: 串流結束時框架缺少結束標記。
        """
        if self._frame is not None:
            raise IncompleteSynchronizedFrame(
                "PTY ended while a synchronized frame was incomplete"
            )
        if self._pending:
            pending = self._pending
            self._pending = ""
            self._publish_visible([pending])

    def _publish_visible(self, characters: list[str]) -> None:
        if characters:
            self._feed("".join(characters))
            self._publish()
