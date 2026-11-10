"""yai-tes storage (SQLite, WAL). The API and worker processes share the file (tes.md section 2).

State writes are conditional updates "only if the current state is from" (tes.md section 4).
Transitions are docs/data/tes-states.yaml copied into code (D-006).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from yai.common.fetch import needs_redact, redact

SCHEMA = Path(__file__).resolve().parent.parent.parent / "schema" / "tes.sql"

log = logging.getLogger("yai.tes")
TERMINAL = frozenset({"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"})
# transitions of docs/data/tes-states.yaml (from -> allowed targets)
TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"INITIALIZING", "CANCELING"}),
    "INITIALIZING": frozenset({"RUNNING", "SYSTEM_ERROR", "CANCELING"}),
    "RUNNING": frozenset({"RUNNING", "EXECUTOR_ERROR", "SYSTEM_ERROR", "COMPLETE", "CANCELING"}),
    "CANCELING": frozenset({"CANCELED"}),
}


# columns added later (also added to old DB files)
_ADDED = {
    "executions": {"exit_code": "INTEGER", "error": "TEXT"},
    "tasks": {"actor": "TEXT"},  # D-067 (replaces on_behalf_of, which stays unused in old files)
    "output_files": {"actor": "TEXT"},
}


class Execution(NamedTuple):
    phase: str  # starting | started | finished
    pid: int | None
    exit_file: str | None
    exit_code: int | None  # result when finished (after a restart this decides)
    error: str | None  # cannot start, timeout or cancel (reason for SYSTEM_ERROR etc.)


def redact_inputs(task: dict[str, Any]) -> bool:
    """Strip query part and userinfo of input signed URLs (system.md section 8). True if changed."""
    changed = False
    for inp in task.get("inputs") or []:
        url = inp.get("url")
        if isinstance(url, str) and needs_redact(url):
            inp["url"] = redact(url)
            changed = True
    return changed


def scrub_finished(task: dict[str, Any]) -> bool:
    """Remove user-sent content from a finished task. True if changed.
    The query part of input URLs (redact_inputs) and inputs[].content per item (D-043).
    The result matches BASIC (TES 1.1 BASIC returns tesInput without content)."""
    changed = redact_inputs(task)
    for inp in task.get("inputs") or []:
        if "content" in inp:
            del inp["content"]
            changed = True
    return changed


def _scrub_ended(db: sqlite3.Connection) -> None:
    """Apply scrub_finished to tasks finished by an older version, and redact_inputs to tasks an
    older version left RUNNING with their signed URLs (at startup, D-043, D-068)."""
    marks = ",".join("?" * len(TERMINAL))
    db.execute("BEGIN IMMEDIATE")
    rows = db.execute(
        f"SELECT id, state, task_json FROM tasks WHERE state IN ({marks}, 'RUNNING')"  # noqa: S608
        " AND (task_json LIKE '%\"content\"%' OR task_json LIKE '%?%' OR task_json LIKE '%@%')",
        tuple(TERMINAL),
    ).fetchall()
    for task_id, state, raw in rows:
        task = json.loads(raw)
        changed = scrub_finished(task) if state in TERMINAL else redact_inputs(task)
        if changed:
            db.execute("UPDATE tasks SET task_json=? WHERE id=?", (json.dumps(task), task_id))
    db.execute("COMMIT")


def _migrate_on_behalf_of(db: sqlite3.Connection) -> None:
    """Files written before D-067 marked delegation with a column on_behalf_of (`<iss>|<sub>` of
    the user; the row's owner was the submitting service) and a tag yai.on_behalf_of. Rewrite
    those rows into the current shape: owner = the user, actor = the service. The old column
    could only be set by a service holding the former tes.delegate scope, so the trust placed in
    it is the same as in `act` today. The tag is dropped (anyone could have sent it). Done once:
    on_behalf_of is cleared afterwards."""
    for table in ("tasks", "output_files"):
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if "on_behalf_of" not in cols:
            continue
        db.execute("BEGIN IMMEDIATE")
        key = "id" if table == "tasks" else "rowid"
        rows = db.execute(
            f"SELECT {key}, owner_iss, owner_sub, on_behalf_of"  # noqa: S608
            f"{', task_json' if table == 'tasks' else ''} FROM {table}"
            " WHERE on_behalf_of IS NOT NULL"
        ).fetchall()
        for row in rows:
            rid, iss, sub, obo = row[:4]
            user_iss, sep, user_sub = str(obo).rpartition("|")
            if not sep or not user_iss or not user_sub:
                log.warning("%s %s: on_behalf_of %r is not <iss>|<sub>; left as is",
                            table, rid, obo)  # fmt: skip
                continue
            actor = f"{iss}|{sub}"
            if table == "tasks":
                task = json.loads(row[4])
                (task.get("tags") or {}).pop("yai.on_behalf_of", None)
                sql = ("UPDATE tasks SET owner_iss=?, owner_sub=?, actor=?, on_behalf_of=NULL,"
                       " task_json=?{} WHERE id=?")  # fmt: skip
                args = (user_iss, user_sub, actor, json.dumps(task), rid)
                try:
                    db.execute(sql.format(""), args)
                except sqlite3.IntegrityError:
                    # the user already has a task with this nonce (tasks_nonce). The nonce only
                    # marks resends, so the old row loses it; the ownership is still rewritten,
                    # consistently with its output rows
                    db.execute(sql.format(", submit_nonce=NULL"), args)
                    log.warning("tasks %s: rewritten without its nonce (it collided with a task "
                                "of %s)", rid, user_sub)  # fmt: skip
            else:
                db.execute(
                    "UPDATE output_files SET owner_iss=?, owner_sub=?, actor=?, on_behalf_of=NULL"
                    " WHERE rowid=?",
                    (user_iss, user_sub, actor, rid),
                )
        if rows:
            log.info("%s: %d rows of an older version rewritten (on_behalf_of -> owner and actor)",
                     table, len(rows))  # fmt: skip
        db.execute("COMMIT")


def _widen_transfer_key(db: sqlite3.Connection) -> None:
    """Rebuild the old output_transfers primary key (task_id, path) as (task_id, path, url)."""
    pk = {r[1] for r in db.execute("PRAGMA table_info(output_transfers)") if r[5]}
    if "url" in pk:
        return
    db.execute("BEGIN IMMEDIATE")
    db.execute("ALTER TABLE output_transfers RENAME TO output_transfers_old")
    db.execute(
        "CREATE TABLE output_transfers (task_id TEXT NOT NULL, path TEXT NOT NULL,"
        " url TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (task_id, path, url))"
    )
    db.execute(
        "INSERT INTO output_transfers SELECT task_id, path, url, done FROM output_transfers_old"
    )
    db.execute("DROP TABLE output_transfers_old")
    db.execute("COMMIT")


@dataclass(frozen=True)
class TaskRow:
    seq: int
    id: str
    owner: tuple[str, str]
    actor: str | None  # <iss>|<sub> of the service that submitted for the owner, else None
    run_id: str | None
    state: str
    name: str | None
    created_at: int
    task: dict[str, Any]
    version: int


class TaskLimit(Exception):
    """Per-user limit of unfinished tasks (create, N-1)."""


class TesDb:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")  # stored in the file, so once
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
            for table, cols in _ADDED.items():
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            _widen_transfer_key(db)
            _migrate_on_behalf_of(db)
            _scrub_ended(db)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Autocommit connection. Always closed after use.
        An exception after BEGIN IMMEDIATE and before COMMIT rolls back on close."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _row(r: tuple) -> TaskRow:
        return TaskRow(
            seq=r[0], id=r[1], owner=(r[2], r[3]), actor=r[4], run_id=r[5],
            state=r[6], name=r[7], created_at=r[8], task=json.loads(r[9]), version=r[10],
        )  # fmt: skip

    _COLS = (
        "seq, id, owner_iss, owner_sub, actor, run_id, state, name, created_at, task_json, version"
    )

    # --- create / read ---
    def task_by_nonce(self, owner: tuple[str, str], nonce: str) -> str | None:
        """The owner's earlier task with this yai.submit_nonce (D-071), if any. create() checks
        again inside its transaction; this lets the API answer a resend before doing any work
        for it (such as resolving drs:// inputs, D-069)."""
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM tasks WHERE owner_iss=? AND owner_sub=? AND submit_nonce=?",
                (owner[0], owner[1], nonce),
            ).fetchone()
        return row[0] if row else None

    def create(
        self,
        task_id: str,
        owner: tuple[str, str],
        task: dict[str, Any],
        now: int,
        *,
        actor: str | None,
        run_id: str | None,
        nonce: str | None,
        max_active: int = 0,
    ) -> str:
        """Create and return the id. If the owner and nonce exist, the earlier id.

        If max_active is positive, raise TaskLimit when the user already has that many unfinished
        tasks (N-1). A user is "owner + actor": tasks submitted directly (no actor) and tasks a
        service submitted on the owner's behalf (per actor, D-067) count separately. The
        count is in the same write (BEGIN IMMEDIATE) as the insert, so concurrent submits cannot
        exceed it. A resend (same nonce) is not counted and returns the earlier id
        (same shape as the WES run limit, D-044)."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if nonce is not None:
                row = db.execute(
                    "SELECT id FROM tasks WHERE owner_iss=? AND owner_sub=? AND submit_nonce=?",
                    (owner[0], owner[1], nonce),
                ).fetchone()
                if row:
                    db.execute("ROLLBACK")
                    return row[0]
            if max_active > 0:
                marks = ",".join("?" * len(TERMINAL))
                sql = (
                    f"SELECT COUNT(*) FROM tasks WHERE state NOT IN ({marks})"  # noqa: S608
                    " AND owner_iss=? AND owner_sub=? AND actor IS ?"
                )
                (active,) = db.execute(
                    sql, (*sorted(TERMINAL), owner[0], owner[1], actor)
                ).fetchone()
                if active >= max_active:
                    db.execute("ROLLBACK")
                    raise TaskLimit(max_active)
            db.execute(
                "INSERT INTO tasks (id, owner_iss, owner_sub, actor, run_id,"
                " submit_nonce, state, name, created_at, task_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (task_id, owner[0], owner[1], actor, run_id, nonce, "QUEUED",
                 task.get("name"), now, json.dumps(task)),
            )  # fmt: skip
            db.execute("COMMIT")
        return task_id

    def get(self, task_id: str) -> TaskRow | None:
        with self.connect() as db:
            r = db.execute(f"SELECT {self._COLS} FROM tasks WHERE id=?", (task_id,)).fetchone()  # noqa: S608
        return self._row(r) if r else None

    def max_seq(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(seq), 0) FROM tasks").fetchone()[0]

    def list_visible(
        self,
        owner: tuple[str, str] | None,
        *,
        snapshot_seq: int,
        after_seq: int | None,
        name_prefix: str | None,
        state: str | None,
        as_actor: str | None = None,
        actor: str | None = None,
    ) -> list[TaskRow]:
        """Visible tasks in descending seq: the owner's (with an exchanged token, actor =
        `<iss>|<act.sub>`, only those that service submitted for them), plus (for a service,
        as_actor = `<iss>|<sub>`) the tasks it submitted for others (D-067). owner=None means
        all (all visible; provisional)."""
        where = ["seq <= ?"]
        args: list[Any] = [snapshot_seq]
        if after_seq is not None:
            where.append("seq < ?")
            args.append(after_seq)
        if owner is not None and actor is not None:
            where.append("((owner_iss=? AND owner_sub=? AND actor=?) OR actor=?)")
            args += [owner[0], owner[1], actor, as_actor or ""]
        elif owner is not None:
            where.append("((owner_iss=? AND owner_sub=?) OR actor=?)")
            args += [owner[0], owner[1], as_actor or ""]
        if name_prefix:
            where.append("substr(name, 1, ?) = ?")
            args += [len(name_prefix), name_prefix]
        if state:
            where.append("state = ?")
            args.append(state)
        sql = f"SELECT {self._COLS} FROM tasks WHERE {' AND '.join(where)} ORDER BY seq DESC"  # noqa: S608
        with self.connect() as db:
            return [self._row(r) for r in db.execute(sql, args).fetchall()]

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return dict(db.execute("SELECT state, COUNT(*) FROM tasks GROUP BY state").fetchall())

    # --- state ---
    def transition(self, task_id: str, frm: str, to: str) -> bool:
        """Set to `to` only if the current state is frm. True if won.

        On a final state, the same write removes the query part of input URLs and inputs[].content
        (no window where they remain after finishing, even on crash; D-043). On INITIALIZING ->
        RUNNING (all inputs fetched; a restart from RUNNING does not fetch again) the same write
        already removes the query part of input URLs: the signed URL, a bearer credential, is not
        kept a moment longer than the fetch needs it (D-068)."""
        if to not in TRANSITIONS.get(frm, frozenset()):
            raise ValueError(f"transition {frm} -> {to} is not in tes-states.yaml")
        with self.connect() as db:
            if to not in TERMINAL and not (frm == "INITIALIZING" and to == "RUNNING"):
                cur = db.execute(
                    "UPDATE tasks SET state=?, version=version+1 WHERE id=? AND state=?",
                    (to, task_id, frm),
                )
                return cur.rowcount == 1
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT task_json FROM tasks WHERE id=? AND state=?", (task_id, frm)
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return False
            task = json.loads(row[0])
            if to in TERMINAL:
                scrub_finished(task)
            else:
                redact_inputs(task)
            db.execute(
                "UPDATE tasks SET state=?, task_json=?, version=version+1 WHERE id=?",
                (to, json.dumps(task), task_id),
            )
            db.execute("COMMIT")
            return True

    def tasks_in(self, states: tuple[str, ...]) -> list[TaskRow]:
        marks = ",".join("?" * len(states))
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {self._COLS} FROM tasks WHERE state IN ({marks}) ORDER BY seq",  # noqa: S608
                states,
            ).fetchall()
        return [self._row(r) for r in rows]

    # --- execution intent / logs ---
    def set_execution(
        self, task_id: str, attempt: int, idx: int, phase: str, now: int,
        pid: int | None = None, exit_file: str | None = None, *,
        exit_code: int | None = None, error: str | None = None,
    ) -> None:  # fmt: skip
        with self.connect() as db:
            db.execute(
                "INSERT INTO executions (task_id, attempt, executor_index, phase, pid, exit_file,"
                " updated_at, exit_code, error) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (task_id, attempt, executor_index)"
                " DO UPDATE SET phase=excluded.phase, pid=COALESCE(excluded.pid, pid),"
                " exit_file=COALESCE(excluded.exit_file, exit_file),"
                " updated_at=excluded.updated_at, exit_code=excluded.exit_code,"
                " error=excluded.error",
                (task_id, attempt, idx, phase, pid, exit_file, now, exit_code, error),
            )

    def executions(self, task_id: str, attempt: int) -> dict[int, Execution]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT executor_index, phase, pid, exit_file, exit_code, error FROM executions"
                " WHERE task_id=? AND attempt=?",
                (task_id, attempt),
            ).fetchall()
        return {r[0]: Execution(*r[1:]) for r in rows}

    def start_log(self, task_id: str, attempt: int, now: int) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO task_logs (task_id, attempt, start_time) VALUES (?,?,?)",
                (task_id, attempt, now),
            )

    def add_system_log(self, task_id: str, attempt: int, line: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO task_logs (task_id, attempt) VALUES (?,?)",
                (task_id, attempt),
            )
            row = db.execute(
                "SELECT system_logs_json FROM task_logs WHERE task_id=? AND attempt=?",
                (task_id, attempt),
            ).fetchone()
            logs = [*json.loads(row[0]), line]
            db.execute(
                "UPDATE task_logs SET system_logs_json=? WHERE task_id=? AND attempt=?",
                (json.dumps(logs), task_id, attempt),
            )

    def add_log_metadata(self, task_id: str, attempt: int, key: str, value: str) -> None:
        """Append to tesTaskLog.metadata. Repeated keys are joined with ","."""
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO task_logs (task_id, attempt) VALUES (?,?)",
                (task_id, attempt),
            )
            row = db.execute(
                "SELECT metadata_json FROM task_logs WHERE task_id=? AND attempt=?",
                (task_id, attempt),
            ).fetchone()
            meta = json.loads(row[0])
            meta[key] = f"{meta[key]},{value}" if meta.get(key) else value
            db.execute(
                "UPDATE task_logs SET metadata_json=? WHERE task_id=? AND attempt=?",
                (json.dumps(meta), task_id, attempt),
            )

    def end_log(self, task_id: str, attempt: int, now: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE task_logs SET end_time=COALESCE(end_time, ?) WHERE task_id=? AND attempt=?",
                (now, task_id, attempt),
            )

    def set_executor_log(
        self, task_id: str, attempt: int, idx: int, *, exit_code: int | None,
        start: int | None, end: int | None, stdout: str, stderr: str,
    ) -> None:  # fmt: skip
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO executor_logs (task_id, attempt, idx, exit_code,"
                " start_time, end_time, stdout_tail, stderr_tail) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, attempt, idx, exit_code, start, end, stdout, stderr),
            )

    def logs(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            tl = db.execute(
                "SELECT attempt, start_time, end_time, metadata_json, system_logs_json"
                " FROM task_logs WHERE task_id=? ORDER BY attempt",
                (task_id,),
            ).fetchall()
            el = db.execute(
                "SELECT attempt, idx, exit_code, start_time, end_time, stdout_tail, stderr_tail"
                " FROM executor_logs WHERE task_id=? ORDER BY attempt, idx",
                (task_id,),
            ).fetchall()
            of = db.execute(
                "SELECT url, path, size_bytes FROM output_files WHERE task_id=? ORDER BY path",
                (task_id,),
            ).fetchall()
        out = []
        for attempt, start, end, meta, sys_logs in tl:
            out.append(
                {
                    "attempt": attempt, "start_time": start, "end_time": end,
                    "metadata": json.loads(meta), "system_logs": json.loads(sys_logs),
                    "executors": [e for e in el if e[0] == attempt],
                    "outputs": of,
                }
            )  # fmt: skip
        return out

    # --- output transfers and records ---
    def plan_transfers(self, task_id: str, outputs: list[tuple[str, str]]) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO output_transfers (task_id, path, url) VALUES (?,?,?)",
                [(task_id, p, u) for p, u in outputs],
            )

    def transfers(self, task_id: str) -> list[tuple[str, str, bool]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT path, url, done FROM output_transfers WHERE task_id=? ORDER BY path, url",
                (task_id,),
            ).fetchall()
        return [(p, u, bool(d)) for p, u, d in rows]

    def output_bytes(self, task_id: str) -> int:
        with self.connect() as db:
            (n,) = db.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM output_files WHERE task_id=?", (task_id,)
            ).fetchone()
        return n

    def record_output(self, row: TaskRow, path: str, url: str, size: int) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO output_files (task_id, url, path, size_bytes, owner_iss, owner_sub,"
                " actor, run_id) VALUES (?,?,?,?,?,?,?,?)",
                (row.id, url, path, size, row.owner[0], row.owner[1], row.actor,
                 row.run_id),
            )  # fmt: skip
            db.execute(
                "UPDATE output_transfers SET done=1 WHERE task_id=? AND path=? AND url=?",
                (row.id, path, url),
            )
            db.execute("COMMIT")

    def output_owner(self, url: str) -> tuple[str, str, str | None, str | None] | None:
        with self.connect() as db:
            r = db.execute(
                "SELECT owner_iss, owner_sub, actor, run_id FROM output_files WHERE url=?",
                (url,),
            ).fetchone()
        return tuple(r) if r else None  # type: ignore[return-value]
