"""Open files under a root without following links (tes.md section 2, "minimal safety").

Each path component is opened with O_NOFOLLOW. Links placed under the root cannot escape it.
"""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import PurePosixPath


class UnsafePath(Exception):
    pass


class MissingSource(UnsafePath):
    """The source file to copy is missing (kept apart from links, unwritable storage etc.;
    wes.md section 1, D-026)."""


class CannotPublish(UnsafePath):
    """Cannot publish on the storage side (exists, link, not a directory, unwritable). One message
    for all reasons: on a shared files_root, replies must not reveal whether another user's file
    exists (D-044).
    The real reason is kept in __cause__ (for server logs)."""

    def __init__(self) -> None:
        super().__init__("cannot publish to the output url")


class TooLarge(UnsafePath):
    """Limit exceeded while copying (total output size)."""


def check_relative(rel: str) -> list[str]:
    parts = [p for p in PurePosixPath(rel).parts if p not in ("", "/")]
    if not parts or any(p in (".", "..") or "\x00" in p for p in parts):
        raise UnsafePath(f"bad path: {rel!r}")
    return parts


def _open_dir(root: str, parts: list[str], *, create: bool) -> int:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o755, dir_fd=fd)
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError as exc:
        os.close(fd)
        raise UnsafePath(f"cannot open directory: {exc.strerror}") from exc
    return fd


def open_beneath(root: str, rel: str, flags: int, mode: int = 0o644, *, mkdirs: bool = False):
    """Open rel under root and return an fd. UnsafePath if any component, or the last, is a link."""
    parts = check_relative(rel)
    dir_fd = _open_dir(root, parts[:-1], create=mkdirs)
    try:
        return os.open(parts[-1], flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    except OSError as exc:
        raise UnsafePath(f"cannot open file: {exc.strerror}") from exc
    finally:
        os.close(dir_fd)


def open_regular_beneath(root: str, rel: str) -> int:
    """Open a regular file under root for reading. FIFOs, devices etc. are UnsafePath (D-042).

    Opened with O_NONBLOCK, so a FIFO without a writer does not block. Checked with fstat."""
    fd = open_beneath(root, rel, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePath("not a regular file")
        os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def ensure_dir_beneath(root: str, rel: str) -> None:
    """Create directory rel under root (UnsafePath on any link on the way; never followed)."""
    os.close(_open_dir(root, check_relative(rel), create=True))


def copy_beneath(
    src_root: str, src_rel: str, dst_root: str, dst_rel: str, *, replace: bool = False
) -> int:
    """Read without following links and copy to a new file (O_EXCL). Returns the size.

    With replace=True, an existing entry (a link, FIFO or file left by a container) is removed by
    name first (never opened, so link targets and FIFO reader waits are untouched; D-041).
    Directories cannot be removed: UnsafePath."""
    try:
        src = open_regular_beneath(src_root, src_rel)
    except UnsafePath as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise MissingSource(str(exc)) from exc
        raise
    try:
        if replace:
            parts = check_relative(dst_rel)
            dir_fd = _open_dir(dst_root, parts[:-1], create=True)
            try:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(parts[-1], dir_fd=dir_fd)
            except OSError as exc:
                raise UnsafePath(f"cannot replace: {exc.strerror}") from exc
            finally:
                os.close(dir_fd)
        dst = open_beneath(
            dst_root, dst_rel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, mkdirs=True
        )
        size = 0
        try:
            while chunk := os.read(src, 1 << 20):
                os.write(dst, chunk)
                size += len(chunk)
        finally:
            os.close(dst)
        return size
    finally:
        os.close(src)


def _part_name(name: str, owner: str) -> str:
    return f".{name}.{owner}.part"


def publish_beneath(
    src_root: str, src_rel: str, dst_root: str, dst_rel: str, owner: str, max_bytes: int = -1
) -> int:
    """Copy an output. Copy to a temporary name .<name>.<owner>.part, then publish
    with link (partial writes never appear under the public name; an existing one is not
    overwritten). Returns the size copied.
    If max_bytes >= 0 and the copy exceeds it, remove the temporary file and raise TooLarge (no
    publish).

    Safe to call again: if our temporary file is the same inode as the public name, it is already
    published (return its size); otherwise treat it as a crashed copy, remove and redo it. After
    publishing and recording, remove it with discard_part.
    """
    parts = check_relative(dst_rel)
    name, part = parts[-1], _part_name(parts[-1], owner)
    try:
        dir_fd = _open_dir(dst_root, parts[:-1], create=True)
    except UnsafePath as exc:
        raise CannotPublish() from exc
    try:
        try:
            mine = os.stat(part, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            mine = None
        if mine is not None:
            try:
                final = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                final = None
            if final is not None and (final.st_dev, final.st_ino) == (mine.st_dev, mine.st_ino):
                return final.st_size  # already published
            os.unlink(part, dir_fd=dir_fd)  # crashed while copying
        try:
            src = open_regular_beneath(src_root, src_rel)
        except UnsafePath as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                raise MissingSource(str(exc)) from exc
            raise
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            try:
                dst = os.open(part, flags, 0o644, dir_fd=dir_fd)
            except OSError as exc:
                raise CannotPublish() from exc
            size = 0
            try:
                while chunk := os.read(src, 1 << 20):
                    size += len(chunk)
                    if 0 <= max_bytes < size:
                        os.unlink(part, dir_fd=dir_fd)
                        raise TooLarge(f"larger than {max_bytes} bytes")
                    os.write(dst, chunk)
                os.fsync(dst)
            finally:
                os.close(dst)
        finally:
            os.close(src)
        try:
            os.link(part, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            os.unlink(part, dir_fd=dir_fd)
            raise CannotPublish() from exc
        os.fsync(dir_fd)
        return size
    finally:
        os.close(dir_fd)


def discard_part(dst_root: str, dst_rel: str, owner: str) -> None:
    """Remove the temporary name of publish_beneath (after recording; may be absent)."""
    parts = check_relative(dst_rel)
    try:
        dir_fd = _open_dir(dst_root, parts[:-1], create=False)
    except UnsafePath:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(_part_name(parts[-1], owner), dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
