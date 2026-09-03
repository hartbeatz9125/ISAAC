from __future__ import annotations

from array import array
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
from uuid import uuid4

from isaac_core.memory.types import MemoryRecord, MemoryStatus, MemoryType


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MemoryRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(database_path, check_same_thread=False, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        self.connection.commit()
        migration_dir = Path(__file__).with_name("migrations")
        for path in sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(path.name.split("_", 1)[0])
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            existing = self.connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version=?", (version,)
            ).fetchone()
            if existing:
                if existing["checksum"] != checksum:
                    raise RuntimeError(f"Memory migration {version} checksum changed")
                continue
            with self.connection:
                self.connection.executescript(sql)
                self.connection.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                    (version, path.name, checksum, utc_now()),
                )

    @staticmethod
    def _record(row: sqlite3.Row, score: float | None = None) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], type=row["type"], content=row["content"],
            created_at=row["created_at"], updated_at=row["updated_at"], source=row["source"],
            confidence=row["confidence"], importance=row["importance"], last_accessed=row["last_accessed"],
            access_count=row["access_count"], expires_at=row["expires_at"], status=row["status"],
            tags=json.loads(row["tags_json"]), related_project=row["related_project"],
            supersedes_id=row["supersedes_id"], score=score,
        )

    @staticmethod
    def normalize(content: str) -> str:
        return re.sub(r"\s+", " ", content.strip()).casefold()

    def create(
        self,
        *,
        memory_type: str,
        content: str,
        source: str,
        confidence: float,
        importance: float,
        tags: list[str] | None = None,
        related_project: str | None = None,
        expires_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> MemoryRecord:
        MemoryType(memory_type)
        now = utc_now()
        memory_id = str(uuid4())
        normalized = self.normalize(content)
        clean_tags = sorted({str(tag).strip().casefold() for tag in (tags or []) if str(tag).strip()})[:20]
        with self._lock, self.connection:
            if supersedes_id:
                self._snapshot(supersedes_id, "superseded")
                self.connection.execute(
                    "UPDATE memories SET status=?, updated_at=? WHERE id=? AND status=?",
                    (MemoryStatus.SUPERSEDED, now, supersedes_id, MemoryStatus.ACTIVE),
                )
            self.connection.execute(
                """INSERT INTO memories(id,type,content,normalized_content,created_at,updated_at,source,confidence,importance,last_accessed,access_count,expires_at,status,tags_json,related_project,supersedes_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (memory_id, memory_type, content.strip(), normalized, now, now, source,
                 float(confidence), float(importance), None, 0, expires_at, MemoryStatus.ACTIVE,
                 json.dumps(clean_tags), related_project, supersedes_id),
            )
        return self.get(memory_id, include_inactive=True)

    def _snapshot(self, memory_id: str, reason: str) -> None:
        row = self.connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            return
        revision = self.connection.execute(
            "SELECT coalesce(max(revision_number),0)+1 FROM memory_revisions WHERE memory_id=?", (memory_id,)
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO memory_revisions(memory_id,revision_number,snapshot_json,changed_at,change_reason) VALUES(?,?,?,?,?)",
            (memory_id, revision, json.dumps(dict(row)), utc_now(), reason),
        )

    def get(self, memory_id: str, include_inactive: bool = False) -> MemoryRecord:
        query = "SELECT * FROM memories WHERE id=?" + ("" if include_inactive else " AND status='active'")
        row = self.connection.execute(query, (memory_id,)).fetchone()
        if not row:
            raise KeyError("Memory not found")
        return self._record(row)

    def list(self, *, status: str = "active", memory_type: str | None = None, limit: int = 100) -> list[MemoryRecord]:
        self.expire_due()
        params: list[Any] = [status]
        query = "SELECT * FROM memories WHERE status=?"
        if memory_type:
            MemoryType(memory_type)
            query += " AND type=?"
            params.append(memory_type)
        query += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        params.append(min(max(int(limit), 1), 500))
        return [self._record(row) for row in self.connection.execute(query, params)]

    def update(self, memory_id: str, **changes: Any) -> MemoryRecord:
        allowed = {"content", "type", "confidence", "importance", "expires_at", "tags", "related_project"}
        changes = {key: value for key, value in changes.items() if key in allowed and value is not None}
        if not changes:
            return self.get(memory_id)
        if "type" in changes:
            MemoryType(changes["type"])
        if "content" in changes:
            changes["normalized_content"] = self.normalize(changes["content"])
        if "tags" in changes:
            changes["tags_json"] = json.dumps(sorted({str(tag).strip().casefold() for tag in changes.pop("tags") if str(tag).strip()}))
        changes["updated_at"] = utc_now()
        with self._lock, self.connection:
            self._snapshot(memory_id, "updated")
            columns = ",".join(f"{key}=?" for key in changes)
            cursor = self.connection.execute(
                f"UPDATE memories SET {columns} WHERE id=? AND status='active'",
                (*changes.values(), memory_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Active memory not found")
            self.connection.execute("DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,))
        return self.get(memory_id)

    def delete(self, memory_id: str, reason: str = "user_request") -> MemoryRecord:
        with self._lock, self.connection:
            self._snapshot(memory_id, reason)
            cursor = self.connection.execute(
                "UPDATE memories SET status='deleted', deletion_reason=?, updated_at=? WHERE id=? AND status='active'",
                (reason, utc_now(), memory_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Active memory not found")
        return self.get(memory_id, include_inactive=True)

    def keyword_search(self, query: str, limit: int = 20) -> list[tuple[MemoryRecord, float]]:
        self.expire_due()
        tokens = re.findall(r"[\w']+", query.casefold())
        if not tokens:
            return []
        expression = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:20])
        rows = self.connection.execute(
            """SELECT memories.*, bm25(memory_fts, 1.0, 0.4, 0.6) AS rank
               FROM memory_fts JOIN memories ON memories.rowid=memory_fts.rowid
               WHERE memory_fts MATCH ? AND memories.status='active'
                 AND (memories.expires_at IS NULL OR memories.expires_at > ?)
               ORDER BY rank LIMIT ?""",
            (expression, utc_now(), limit),
        ).fetchall()
        count = max(len(rows), 1)
        return [(self._record(row), 1.0 - (index / count)) for index, row in enumerate(rows)]

    def active_with_embeddings(self, model: str) -> list[tuple[MemoryRecord, list[float]]]:
        self.expire_due()
        rows = self.connection.execute(
            """SELECT memories.*, memory_embeddings.vector, memory_embeddings.dimensions
               FROM memories JOIN memory_embeddings ON memory_embeddings.memory_id=memories.id
               WHERE memories.status='active' AND memory_embeddings.model=?
                 AND (memories.expires_at IS NULL OR memories.expires_at > ?)""",
            (model, utc_now()),
        ).fetchall()
        results = []
        for row in rows:
            vector = array("f")
            vector.frombytes(row["vector"])
            results.append((self._record(row), list(vector)))
        return results

    def set_embedding(self, memory_id: str, model: str, vector: list[float]) -> None:
        packed = array("f", vector).tobytes()
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO memory_embeddings(memory_id,model,dimensions,vector,updated_at) VALUES(?,?,?,?,?)",
                (memory_id, model, len(vector), packed, utc_now()),
            )

    def mark_accessed(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        with self.connection:
            self.connection.executemany(
                "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                [(utc_now(), memory_id) for memory_id in memory_ids],
            )

    def expire_due(self) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE memories SET status='expired', updated_at=? WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?",
                (utc_now(), utc_now()),
            )

    def close(self) -> None:
        self.connection.close()
