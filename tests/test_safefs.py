"""Unit tests for safefs (open without following links). replace and ensure_dir_beneath came
from D-041."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from yai.common.safefs import (
    UnsafePath,
    copy_beneath,
    ensure_dir_beneath,
    open_beneath,
    open_regular_beneath,
    publish_beneath,
)
from yai.tes.runner import read_tail


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    src, dst, outside = tmp_path / "src", tmp_path / "dst", tmp_path / "outside"
    for d in (src, dst, outside):
        d.mkdir()
    (src / "log").write_text("fresh")
    (outside / "victim").write_text("keep")
    return src, dst, outside


def test_copy_beneath_refuses_to_overwrite_without_replace(roots):
    src, dst, _ = roots
    assert copy_beneath(str(src), "log", str(dst), "a/b/out") == 5
    assert (dst / "a/b/out").read_text() == "fresh"
    with pytest.raises(UnsafePath):
        copy_beneath(str(src), "log", str(dst), "a/b/out")
    (src / "log").write_text("newer")
    assert copy_beneath(str(src), "log", str(dst), "a/b/out", replace=True) == 5
    assert (dst / "a/b/out").read_text() == "newer"


def test_replace_removes_a_symlink_by_name_and_never_writes_through_it(roots):
    src, dst, outside = roots
    (dst / "out").symlink_to(outside / "victim")
    copy_beneath(str(src), "log", str(dst), "out", replace=True)
    assert (outside / "victim").read_text() == "keep"
    assert not (dst / "out").is_symlink() and (dst / "out").read_text() == "fresh"


def test_replace_does_not_open_a_fifo_and_cannot_replace_a_directory(roots):
    src, dst, _ = roots
    os.mkfifo(dst / "out")  # opening would block on a reader; it is removed by name, so no block
    copy_beneath(str(src), "log", str(dst), "out", replace=True)
    assert (dst / "out").read_text() == "fresh"
    (dst / "d").mkdir()
    with pytest.raises(UnsafePath, match="cannot replace"):
        copy_beneath(str(src), "log", str(dst), "d", replace=True)


def test_symlinked_intermediate_directory_is_refused_even_with_replace(roots):
    src, dst, outside = roots
    (dst / "link").symlink_to(outside)
    with pytest.raises(UnsafePath):
        copy_beneath(str(src), "log", str(dst), "link/out", replace=True)
    assert not (outside / "out").exists()
    with pytest.raises(UnsafePath):
        ensure_dir_beneath(str(dst), "link/deep")
    assert not (outside / "deep").exists()


def test_ensure_dir_beneath_creates_plain_directories(roots):
    _, dst, _ = roots
    ensure_dir_beneath(str(dst), "a/b/c")
    assert (dst / "a/b/c").is_dir()
    ensure_dir_beneath(str(dst), "a/b/c")  # a second call also passes
    fd = open_beneath(str(dst), "a/b/c/f", os.O_WRONLY | os.O_CREAT)
    os.close(fd)
    assert (dst / "a/b/c/f").is_file()


def _within(seconds: float, fn) -> object:
    """Run fn in a thread, check it returns within seconds, return its result (or exception)."""
    out: list[object] = []

    def call():
        try:
            out.append(fn())
        except Exception as exc:
            out.append(exc)

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "blocked (opened a FIFO waiting for a reader/writer)"
    return out[0]


def test_NEG_FIFO_SOURCE(roots):
    """A FIFO as copy source or published output gives
    UnsafePath without waiting.
    Previously it was opened O_RDONLY and waited for a writer, blocking the run slot (needed
    cancel + restart)."""
    src, dst, _ = roots
    os.mkfifo(src / "fifo")
    for fn in (lambda: open_regular_beneath(str(src), "fifo"),
               lambda: copy_beneath(str(src), "fifo", str(dst), "a"),
               lambda: publish_beneath(str(src), "fifo", str(dst), "b", "t1")):  # fmt: skip
        err = _within(2, fn)
        assert isinstance(err, UnsafePath) and "not a regular file" in str(err)
    assert not (dst / "a").exists() and not (dst / "b").exists()
    assert _within(2, lambda: read_tail(src / "fifo")) == "[not a regular file]"
    fd = open_regular_beneath(str(src), "log")
    try:
        assert os.get_blocking(fd) and os.read(fd, 10) == b"fresh"
    finally:
        os.close(fd)


def test_publish_stops_at_max_bytes(tmp_path):
    """Over max_bytes during the copy: not published and no temp name left (#30). Exactly at the
    limit is copied."""
    from yai.common.safefs import TooLarge

    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "f").write_bytes(b"x" * (3 << 20))
    with pytest.raises(TooLarge):
        publish_beneath(str(src), "f", str(dst), "big", "t1", max_bytes=(2 << 20) + 1)
    assert list(dst.iterdir()) == []
    assert publish_beneath(str(src), "f", str(dst), "ok", "t1", max_bytes=3 << 20) == 3 << 20
