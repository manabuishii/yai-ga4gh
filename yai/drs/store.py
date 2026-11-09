"""yai-drs storage (design/drs.md section 5): content-addressed file store and SQLite.

Immutable once registered (DRS-ID-02). Content is copied on registration, so later edits to the
original file do not change it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from yai.common.ids import new_id

SCHEMA = Path(__file__).resolve().parent.parent.parent / "schema" / "drs.sql"
POLICIES = ("public", "bearer", "dataset")


@dataclass(frozen=True)
class DrsRecord:
    id: str
    sha256: str
    size: int
    name: str | None
    mime_type: str | None
    created_time: int
    created_time_source: str
    registered_at: int
    description: str | None
    metadata_policy: str
    access_policy: str
    dataset: str | None
    checksums: tuple[tuple[str, str], ...]


class DrsStore:
    def __init__(self, root: Path):
        self.root = root
        self.blobs = root / "blobs" / "sha256"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "drs.sqlite"
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")  # stored in the file, so once
            db.executescript(SCHEMA.read_text(encoding="utf-8"))

    @contextmanager
    def _db(self) -> Generator[sqlite3.Connection, None, None]:
        """End the transaction and always close the connection."""
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def blob_path(self, sha256: str) -> Path:
        return self.blobs / sha256[:2] / sha256

    def register(
        self,
        source: Path,
        *,
        metadata_policy: str,
        access_policy: str,
        dataset: str | None,
        now: int,
        name: str | None = None,
        mime_type: str | None = None,
        created_time: int | None = None,
        description: str | None = None,
    ) -> str:
        if metadata_policy not in POLICIES or access_policy not in POLICIES:
            raise ValueError("policy must be one of public, bearer, dataset")
        if "dataset" in (metadata_policy, access_policy) and not dataset:
            raise ValueError("dataset policy needs --dataset")
        mtime = int(source.stat().st_mtime)
        sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
        tmp = tempfile.NamedTemporaryFile(dir=self.root, delete=False)  # noqa: SIM115
        try:
            with open(source, "rb") as src, tmp:
                while chunk := src.read(1 << 20):
                    sha.update(chunk)
                    md5.update(chunk)
                    tmp.write(chunk)
                    size += len(chunk)
            digest = sha.hexdigest()
            dest = self.blob_path(digest)
            dest.parent.mkdir(exist_ok=True)
            if dest.exists():
                os.unlink(tmp.name)  # identical content is stored only once
            else:
                os.chmod(tmp.name, 0o444)
                shutil.move(tmp.name, dest)
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
        object_id = new_id("o")
        with self._db() as db:
            db.execute(
                "INSERT INTO objects (id, sha256, size, name, mime_type, created_time,"
                " created_time_source, registered_at, description, metadata_policy,"
                " access_policy, dataset) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    object_id, digest, size, name or source.name, mime_type,
                    created_time if created_time is not None else mtime,
                    "given" if created_time is not None else "file_mtime",
                    now, description, metadata_policy, access_policy, dataset,
                ),
            )  # fmt: skip
            db.executemany(
                "INSERT INTO checksums (object_id, type, checksum) VALUES (?,?,?)",
                [(object_id, "sha-256", digest), (object_id, "md5", md5.hexdigest())],
            )
        return object_id

    def get(self, object_id: str) -> DrsRecord | None:
        with self._db() as db:
            row = db.execute(
                "SELECT id, sha256, size, name, mime_type, created_time, created_time_source,"
                " registered_at, description, metadata_policy, access_policy, dataset"
                " FROM objects WHERE id = ? AND deleted = 0",
                (object_id,),
            ).fetchone()
            if row is None:
                return None
            sums = db.execute(
                "SELECT type, checksum FROM checksums WHERE object_id = ? ORDER BY type",
                (object_id,),
            ).fetchall()
        return DrsRecord(*row, checksums=tuple(sums))

    def set_dataset(self, object_id: str, dataset: str) -> None:
        """Change an object's dataset assignment (an operations task).

        Content and ID do not change (DRS-ID-02)."""
        with self._db() as db:
            cur = db.execute(
                "UPDATE objects SET dataset = ? WHERE id = ? AND deleted = 0", (dataset, object_id)
            )
            if cur.rowcount != 1:
                raise KeyError(object_id)

    def counts(self) -> tuple[int, int]:
        with self._db() as db:
            n = db.execute("SELECT COUNT(*) FROM objects WHERE deleted = 0").fetchone()[0]
            total = db.execute(
                "SELECT COALESCE(SUM(size), 0) FROM (SELECT DISTINCT sha256, size FROM objects"
                " WHERE deleted = 0)"
            ).fetchone()[0]
        return n, total
