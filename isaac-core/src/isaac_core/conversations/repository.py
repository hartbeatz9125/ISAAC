from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConversationRepository:
    """Transcript storage in ISAAC's database, separate from long-term memory."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(database_path, check_same_thread=False, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, object]:
        return {
            "conversationId": row["id"], "title": row["title"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "lastActiveAt": row["last_active_at"],
        }

    def create(self, title: str = "New Chat") -> dict[str, object]:
        conversation_id, now = str(uuid4()), utc_now()
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO conversations(id,title,created_at,updated_at,last_active_at) VALUES(?,?,?,?,?)",
                (conversation_id, title, now, now, now),
            )
        return self.get(conversation_id)

    def get(self, conversation_id: str) -> dict[str, object]:
        row = self.connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row:
            raise KeyError("Conversation not found")
        result = self._public(row)
        result["messages"] = [
            {"role": item["role"], "content": item["content"], "createdAt": item["created_at"]}
            for item in self.connection.execute(
                "SELECT role,content,created_at FROM conversation_messages WHERE conversation_id=? ORDER BY sequence",
                (conversation_id,),
            )
        ]
        return result

    def list(self) -> list[dict[str, object]]:
        rows = self.connection.execute("SELECT * FROM conversations ORDER BY last_active_at DESC").fetchall()
        return [self._public(row) for row in rows]

    def latest(self) -> dict[str, object] | None:
        row = self.connection.execute("SELECT id FROM conversations ORDER BY last_active_at DESC LIMIT 1").fetchone()
        return self.get(row["id"]) if row else None

    def touch(self, conversation_id: str) -> None:
        now = utc_now()
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE conversations SET last_active_at=?,updated_at=? WHERE id=?", (now, now, conversation_id)
            )
            if cursor.rowcount != 1:
                raise KeyError("Conversation not found")

    def append(self, conversation_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("Only user and assistant transcript messages may be stored")
        now = utc_now()
        with self._lock, self.connection:
            sequence = self.connection.execute(
                "SELECT coalesce(max(sequence),0)+1 FROM conversation_messages WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO conversation_messages(id,conversation_id,role,content,created_at,sequence) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), conversation_id, role, content, now, sequence),
            )
            self.connection.execute(
                "UPDATE conversations SET updated_at=?,last_active_at=? WHERE id=?", (now, now, conversation_id)
            )

    def pop_last(self, conversation_id: str, role: str) -> None:
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT id FROM conversation_messages WHERE conversation_id=? AND role=? ORDER BY sequence DESC LIMIT 1",
                (conversation_id, role),
            ).fetchone()
            if row:
                self.connection.execute("DELETE FROM conversation_messages WHERE id=?", (row["id"],))

    def rename(self, conversation_id: str, title: str) -> dict[str, object]:
        clean = " ".join(title.split())[:80]
        if not clean:
            raise ValueError("Conversation title is required")
        now = utc_now()
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE id=?", (clean, now, conversation_id)
            )
            if cursor.rowcount != 1:
                raise KeyError("Conversation not found")
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> None:
        with self._lock, self.connection:
            cursor = self.connection.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            if cursor.rowcount != 1:
                raise KeyError("Conversation not found")

    def close(self) -> None:
        self.connection.close()
