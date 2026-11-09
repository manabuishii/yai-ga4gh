"""yai-wes storage (SQLite). State writes are conditional updates (wes.md section 4).

Transitions are docs/data/wes-states.yaml copied into code (D-006).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yai.common.fetch import needs_redact, redact
from yai.common.logsetup import redact as redact_text

SCHEMA = Path(__file__).resolve().parent.parent.parent / "schema" / "wes.sql"

TERMINAL = frozenset({"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"INITIALIZING", "CANCELED"}),
    "INITIALIZING": frozenset({"RUNNING", "SYSTEM_ERROR", "CANCELING"}),
    "RUNNING": frozenset({"RUNNING", "COMPLETE", "SYSTEM_ERROR", "EXECUTOR_ERROR", "CANCELING"}),
    "CANCELING": frozenset({"CANCELED"}),
}


@dataclass(frozen=True)
class RunRow:
    seq: int
    id: str
    owner: tuple[str, str]
    state: str
    request: dict[str, Any]
    needs_passport: bool
    created_at: int
    start_time: int | None
    end_time: int | None
    outputs: dict[str, Any] | None
    system_logs: list[str]


@dataclass(frozen=True)
class StepRow:
    run_id: str
    n: int
    name: str
    phase: str
    submit_nonce: str | None
    tes_task_id: str | None
    state: str | None
    cmd: list[str]
    exit_code: int | None
    start_time: int | None
    end_time: int | None
    unreachable_since: int | None = None
    cancel_pending: bool = False
    outputs: dict[str, Any] | None = None  # recorded at COMPLETE (next step's inputs)


_RUN = (
    "seq, id, owner_iss, owner_sub, state, request_json, needs_passport, created_at, start_time,"
    " end_time, outputs_json, system_logs_json"
)
_STEP = (
    "run_id, n, name, phase, submit_nonce, tes_task_id, state, cmd_json, exit_code, start_time,"
    " end_time, unreachable_since, cancel_pending, outputs_json"
)
# columns added later (also added to old DB files)
_ADDED = {
    "unreachable_since": "INTEGER",
    "cancel_pending": "INTEGER NOT NULL DEFAULT 0",
    "outputs_json": "TEXT",
}


class RunLimit(Exception):
    """Per-user limit of unfinished runs (create_run)."""


def scrub_request(request: dict[str, Any]) -> bool:
    """Remove signed URL query parts and userinfo from request (D-030 opt. A, #8). True if changed.

    Removes workflow_url and the location/path of Files in workflow_params (drs:// too).
    They are needed to run, so kept until the run ends (removed in the final-state write)."""
    changed = False

    def clean(value: Any) -> Any:
        nonlocal changed
        if isinstance(value, str) and "://" in value and needs_redact(value):
            changed = True
            return redact(value)
        return value

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            out = {k: walk(v) for k, v in value.items()}
            if out.get("class") == "File":
                for key in ("location", "path"):
                    if key in out:
                        out[key] = clean(out[key])
            return out
        return value

    if "workflow_url" in request:
        request["workflow_url"] = clean(request["workflow_url"])
    if "workflow_params" in request:
        request["workflow_params"] = walk(request["workflow_params"])
    return changed


def _active_runs(db: sqlite3.Connection, owner: tuple[str, str]) -> int:
    marks = ",".join("?" * len(TERMINAL))
    sql = f"SELECT COUNT(*) FROM runs WHERE state NOT IN ({marks})"  # noqa: S608
    (active,) = db.execute(
        sql + " AND owner_iss=? AND owner_sub=?", (*TERMINAL, owner[0], owner[1])
    ).fetchone()
    return int(active)


def _run(r: tuple) -> RunRow:
    return RunRow(
        seq=r[0], id=r[1], owner=(r[2], r[3]), state=r[4], request=json.loads(r[5]),
        needs_passport=bool(r[6]), created_at=r[7], start_time=r[8], end_time=r[9],
        outputs=json.loads(r[10]) if r[10] else None, system_logs=json.loads(r[11]),
    )  # fmt: skip


def _step(r: tuple) -> StepRow:
    return StepRow(
        run_id=r[0], n=r[1], name=r[2], phase=r[3], submit_nonce=r[4], tes_task_id=r[5],
        state=r[6], cmd=json.loads(r[7]), exit_code=r[8], start_time=r[9], end_time=r[10],
        unreachable_since=r[11], cancel_pending=bool(r[12]),
        outputs=json.loads(r[13]) if r[13] else None,
    )  # fmt: skip


class WesDb:
    def __init__(self, path: Path, runs_dir: Path | None = None):
        self.path = path
        self.runs_dir = runs_dir  # per-run saved documents and attachments (runs/<id>/)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")  # stored in the file, so once
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
            have = {r[1] for r in db.execute("PRAGMA table_info(steps)")}
            for col, decl in _ADDED.items():
                if col not in have:
                    db.execute(f"ALTER TABLE steps ADD COLUMN {col} {decl}")
            self._scrub_finished(db)
            self._sweep_files(db)

    @staticmethod
    def _scrub_finished(db: sqlite3.Connection) -> None:
        """Also scrub requests of runs finished by older versions (checked each startup, D-030)."""
        marks = ",".join("?" * len(TERMINAL))
        rows = db.execute(
            f"SELECT id, request_json FROM runs WHERE state IN ({marks})",  # noqa: S608
            tuple(TERMINAL),
        ).fetchall()
        for run_id, raw in rows:
            request = json.loads(raw)
            if scrub_request(request):
                db.execute(
                    "UPDATE runs SET request_json=? WHERE id=?", (json.dumps(request), run_id)
                )

    def _sweep_files(self, db: sqlite3.Connection) -> None:
        """Remove runs/<id>/ of finished runs and of runs not in the DB (left by a crashed submit)
        (also runs finished by older versions and runs that crashed between the final write and
        removal, D-030).
        Documents and attachments of unfinished runs are kept; resuming after restart needs them."""
        if self.runs_dir is None or not self.runs_dir.is_dir():
            return
        live = {r[0] for r in db.execute(
            f"SELECT id FROM runs WHERE state NOT IN ({','.join('?' * len(TERMINAL))})",  # noqa: S608
            tuple(TERMINAL),
        )}  # fmt: skip
        for d in self.runs_dir.iterdir():
            if d.name not in live:
                self._drop_files(d.name)

    def _drop_files(self, run_id: str) -> None:
        """Remove a run's documents and attachments (runs/<id>/), signed URLs included (D-030)."""
        if self.runs_dir is not None:
            shutil.rmtree(self.runs_dir / run_id, ignore_errors=True)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Autocommit connection. Always closed after use."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            yield db
        finally:
            db.close()

    # --- run ---
    def create_run(
        self, run_id: str, owner: tuple[str, str], request: dict[str, Any], now: int,
        needs_passport: bool, steps: list[tuple[str, list[str]]], max_active: int = 0,
    ) -> None:  # fmt: skip
        """Create the run and all steps (phase=planned) in one write. Steps numbered from 1.
        If max_active is positive, raise RunLimit when the owner already has that many unfinished
        runs (counted in the same write, so concurrent submits cannot exceed it; D-044)."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if max_active > 0 and _active_runs(db, owner) >= max_active:
                db.execute("ROLLBACK")
                raise RunLimit(max_active)
            db.execute(
                "INSERT INTO runs (id, owner_iss, owner_sub, state, request_json, needs_passport,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, owner[0], owner[1], "QUEUED", json.dumps(request), int(needs_passport),
                 now),
            )  # fmt: skip
            db.executemany(
                "INSERT INTO steps (run_id, n, name, phase, cmd_json) VALUES (?,?,?,?,?)",
                [(run_id, n, name, "planned", json.dumps(cmd))
                 for n, (name, cmd) in enumerate(steps, 1)],
            )  # fmt: skip
            db.execute("COMMIT")

    def active_runs(self, owner: tuple[str, str]) -> int:
        """Number of the owner's unfinished runs (checked on submit before any fetch, D-057).
        The final check is in the same write as create_run."""
        with self.connect() as db:
            return _active_runs(db, owner)

    def get_run(self, run_id: str) -> RunRow | None:
        with self.connect() as db:
            r = db.execute(f"SELECT {_RUN} FROM runs WHERE id=?", (run_id,)).fetchone()  # noqa: S608
        return _run(r) if r else None

    def runs_in(self, states: tuple[str, ...]) -> list[RunRow]:
        marks = ",".join("?" * len(states))
        sql = f"SELECT {_RUN} FROM runs WHERE state IN ({marks}) ORDER BY seq"  # noqa: S608
        with self.connect() as db:
            return [_run(r) for r in db.execute(sql, states).fetchall()]

    def max_seq(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()[0]

    def list_runs(
        self, owner: tuple[str, str], snapshot_seq: int, after_seq: int | None, limit: int
    ) -> list[RunRow]:
        where, args = "owner_iss=? AND owner_sub=? AND seq<=?", [owner[0], owner[1], snapshot_seq]
        if after_seq is not None:
            where += " AND seq<?"
            args.append(after_seq)
        sql = f"SELECT {_RUN} FROM runs WHERE {where} ORDER BY seq DESC LIMIT ?"  # noqa: S608
        with self.connect() as db:
            return [_run(r) for r in db.execute(sql, [*args, limit]).fetchall()]

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return dict(db.execute("SELECT state, COUNT(*) FROM runs GROUP BY state").fetchall())

    def transition(self, run_id: str, frm: str, to: str, now: int) -> bool:
        if to not in TRANSITIONS.get(frm, frozenset()):
            raise ValueError(f"transition {frm} -> {to} is not in wes-states.yaml")
        start = now if frm == "QUEUED" and to != "CANCELED" else None
        end = now if to in TERMINAL else None
        with self.connect() as db:
            if to not in TERMINAL:
                cur = db.execute(
                    "UPDATE runs SET state=?, version=version+1,"
                    " start_time=COALESCE(start_time, ?) WHERE id=? AND state=?",
                    (to, start, run_id, frm),
                )
                return cur.rowcount == 1
            # final state and the request with signed URLs removed in one write (D-030 option A).
            # Split writes would expose the URL on a crash or read in between
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_json FROM runs WHERE id=? AND state=?", (run_id, frm)
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return False
            request = json.loads(row[0])
            scrub_request(request)
            db.execute(
                "UPDATE runs SET state=?, request_json=?, version=version+1,"
                " start_time=COALESCE(start_time, ?), end_time=COALESCE(end_time, ?)"
                " WHERE id=?",
                (to, json.dumps(request), start, end, run_id),
            )
            db.execute("COMMIT")
        # documents and attachments are files and cannot share the write. Remove after the final
        # write (removing first loses the document of a run that lost the conditional update). On
        # a crash in between, they are removed at startup
        self._drop_files(run_id)
        return True

    def add_log(self, run_id: str, line: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT system_logs_json FROM runs WHERE id=?", (run_id,)).fetchone()
            logs = [*json.loads(row[0]), redact_text(line)]  # no query part in the record
            db.execute("UPDATE runs SET system_logs_json=? WHERE id=?", (json.dumps(logs), run_id))
            db.execute("COMMIT")

    def set_outputs(self, run_id: str, outputs: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET outputs_json=? WHERE id=?", (json.dumps(outputs), run_id))

    # --- step ---
    def steps(self, run_id: str) -> list[StepRow]:
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {_STEP} FROM steps WHERE run_id=? ORDER BY n",  # noqa: S608
                (run_id,),
            ).fetchall()
        return [_step(r) for r in rows]

    def pending_stops(self) -> list[str]:
        """Finished runs whose TES tasks are not yet fully stopped (picked up on restart)."""
        with self.connect() as db:
            rows = db.execute("SELECT DISTINCT run_id FROM steps WHERE cancel_pending=1").fetchall()
        return [r[0] for r in rows]

    def update_step(self, run_id: str, n: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.connect() as db:
            db.execute(
                f"UPDATE steps SET {cols} WHERE run_id=? AND n=?",  # noqa: S608
                (*fields.values(), run_id, n),
            )
