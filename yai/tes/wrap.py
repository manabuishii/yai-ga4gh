"""Small process wrapping an executor: runs the child and writes its exit code to a file.

Even if the worker restarts, the exit code file tells the result (D-024, restart in tes.md
section 4).
Before running the child, it writes a start marker `started` (own PID, process start time, clock
start time) to the same directory with O_EXCL. If the marker exists, it exits without running
anything (a second wrap never runs the executor, so there is no double run even if the worker
crashes between Popen and saving the PID).
Usage: python -m yai.tes.wrap <exit_file> -- argv...
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from yai.tes.runner import ALREADY_STARTED, proc_start


def _durable_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def main(argv: list[str]) -> int:
    exit_file = argv[1]
    assert argv[2] == "--"
    marker = os.path.join(os.path.dirname(exit_file), "started")
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return ALREADY_STARTED  # a wrap started earlier is there
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {proc_start(os.getpid())} {int(time.time())}\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        code = subprocess.call(argv[3:])  # noqa: S603 the executor's argv
    except OSError:
        code = 127
    _durable_write(exit_file, str(code))  # do not lose the exit code
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
