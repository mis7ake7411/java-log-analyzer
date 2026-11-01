from __future__ import annotations

import sys
from pathlib import Path

import pytest


PORTABLE_TOOLS = Path(__file__).resolve().parents[2] / "tools" / "portable"
sys.path.insert(0, str(PORTABLE_TOOLS))

from frame_stream import (  # noqa: E402
    IncompleteSynchronizedFrame,
    TerminalFrameStream,
)


QUERY = "\x1b[?2026$p"
REPLY = "\x1b[?2026;2$y"
SYNC_START = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"


class _Screen:
    def __init__(self):
        self.value = ""
        self.feed_calls: list[str] = []
        self.published: list[str] = []
        self.replies: list[str] = []

    def feed(self, text: str) -> None:
        self.feed_calls.append(text)
        if "\x1b[2J" in text:
            self.value = text.split("\x1b[2J", maxsplit=1)[1]
        else:
            self.value += text

    def adapter(self) -> TerminalFrameStream:
        return TerminalFrameStream(
            feed=self.feed,
            send=self.replies.append,
            publish=lambda: self.published.append(self.value),
        )


def test_split_synchronized_output_query_gets_one_reply_and_preserves_adjacent_text():
    screen = _Screen()
    terminal = screen.adapter()

    terminal.feed("before" + QUERY[:5])
    terminal.feed(QUERY[5:9])
    terminal.feed(QUERY[9:] + "after")

    assert screen.replies == [REPLY]
    assert screen.feed_calls == ["before", "after"]
    assert screen.published == ["before", "beforeafter"]


def test_split_frame_markers_keep_old_screen_until_complete_frame_is_published():
    screen = _Screen()
    terminal = screen.adapter()
    terminal.feed("old")

    terminal.feed(SYNC_START[:5])
    terminal.feed(SYNC_START[5:] + "\x1b[2Jnew" + SYNC_END[:6])

    assert screen.value == "old"
    assert screen.feed_calls == ["old"]
    assert screen.published == ["old"]

    terminal.feed(SYNC_END[6:])

    assert screen.value == "new"
    assert screen.feed_calls == ["old", "\x1b[2Jnew"]
    assert screen.published == ["old", "new"]


def test_consecutive_frames_publish_once_each_and_other_ansi_is_unchanged():
    screen = _Screen()
    terminal = screen.adapter()
    ordinary_ansi = "\x1b[31mred\x1b[0m"

    terminal.feed(ordinary_ansi)
    terminal.feed(SYNC_START + "first" + SYNC_END + SYNC_START + "second" + SYNC_END)

    assert screen.feed_calls == [ordinary_ansi, "first", "second"]
    assert screen.published == [ordinary_ansi, ordinary_ansi + "first", ordinary_ansi + "firstsecond"]


def test_unmatched_sync_end_remains_in_the_terminal_stream():
    screen = _Screen()
    terminal = screen.adapter()

    terminal.feed(SYNC_END)

    assert screen.feed_calls == [SYNC_END]
    assert screen.published == [SYNC_END]


def test_incomplete_synchronized_frame_fails_without_publishing_partial_content():
    screen = _Screen()
    terminal = screen.adapter()
    terminal.feed(SYNC_START + "partial redraw")

    with pytest.raises(IncompleteSynchronizedFrame, match="incomplete"):
        terminal.finish()

    assert screen.feed_calls == []
    assert screen.published == []
