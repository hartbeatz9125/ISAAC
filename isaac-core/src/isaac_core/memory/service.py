from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from isaac_core.memory.embeddings import LocalEmbeddingService
from isaac_core.memory.policy import MemoryDirective, parse_directive, secret_reason
from isaac_core.memory.repository import MemoryRepository
from isaac_core.memory.types import MemoryRecord, MemoryType

MemoryEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


class MemoryService:
    _QUERY_STOPWORDS = {
        "a", "about", "am", "an", "and", "are", "bro", "do", "i", "i'm", "is", "it", "me", "my", "of", "on", "or", "test", "the", "thinking", "to", "unrelated", "up", "what", "what's", "you", "your", "yo"
    }
    _RECALL_INTENT = re.compile(r"\b(?:remember|recall|what(?:'s|\s+is)\s+my|what\s+did\s+i\s+(?:tell|ask)|do\s+you\s+(?:know|remember)|memory\s+test)\b", re.I)
    def __init__(self, repository: MemoryRepository | None, embedder: LocalEmbeddingService, emit: MemoryEvent, error: str | None = None) -> None:
        self.repository = repository
        self.embedder = embedder
        self.emit = emit
        self.error = error

    @classmethod
    def open(cls, emit: MemoryEvent) -> "MemoryService":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ISAAC" / "Core"
        database_path = Path(os.environ.get("ISAAC_MEMORY_DB", local / "isaac.db"))
        try:
            return cls(MemoryRepository(database_path), LocalEmbeddingService(), emit)
        except Exception as error:
            return cls(None, LocalEmbeddingService(), emit, str(error))

    @property
    def available(self) -> bool:
        return self.repository is not None

    def status(self) -> dict[str, Any]:
        return {
            "state": "ready" if self.available else "unavailable",
            "databasePath": str(self.repository.database_path) if self.repository else None,
            "schemaVersion": 1 if self.repository else None,
            "embeddingModel": self.embedder.MODEL_ID,
            "error": self.error,
        }

    @staticmethod
    def directive(message: str) -> MemoryDirective | None:
        return parse_directive(message)

    async def create(self, **values: Any) -> MemoryRecord:
        if not self.repository:
            raise RuntimeError("Memory service is unavailable")
        content = str(values.get("content", "")).strip()
        if not content:
            raise ValueError("Memory content is required")
        if len(content) > 8000:
            raise ValueError("Memory content is too long")
        unsafe = secret_reason(content)
        if unsafe:
            raise ValueError(unsafe)
        record = self.repository.create(
            memory_type=values.get("memory_type", MemoryType.EXPLICIT),
            content=content,
            source=values.get("source", "user_explicit"),
            confidence=float(values.get("confidence", 1.0)),
            importance=float(values.get("importance", 0.9)),
            tags=values.get("tags") or [],
            related_project=values.get("related_project"),
            expires_at=values.get("expires_at"),
            supersedes_id=values.get("supersedes_id"),
        )
        await self._embed_record(record)
        await self.emit("isaac.memory.created", {"id": record.id, "type": record.type})
        if record.supersedes_id:
            await self.emit("isaac.memory.updated", {"id": record.id, "supersedesId": record.supersedes_id})
        return record

    async def _embed_record(self, record: MemoryRecord) -> None:
        if not self.repository:
            return
        try:
            vector = (await self.embedder.embed([record.content], query=False))[0]
            self.repository.set_embedding(record.id, self.embedder.MODEL_ID, vector)
        except Exception as error:
            await self.emit("isaac.memory.error", {"code": "embedding_failed", "message": str(error), "recoverable": True})

    async def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        if not self.repository:
            return []
        raw_query_tokens = set(re.findall(r"[\w']+", query.casefold()))
        query_tokens = {token for token in raw_query_tokens if len(token) > 1 and token not in self._QUERY_STOPWORDS}
        recall_intent = bool(self._RECALL_INTENT.search(query))
        keyword_query = " ".join(sorted(query_tokens))
        keyword = self.repository.keyword_search(keyword_query, limit=30) if keyword_query else []
        keyword_scores = {record.id: score for record, score in keyword}
        candidates = {record.id: record for record, _score in keyword}
        semantic_scores: dict[str, float] = {}
        try:
            query_vector = (await self.embedder.embed([query], query=True))[0]
            for record, vector in self.repository.active_with_embeddings(self.embedder.MODEL_ID):
                score = sum(a * b for a, b in zip(query_vector, vector))
                semantic_scores[record.id] = score
                candidates[record.id] = record
        except Exception as error:
            await self.emit("isaac.memory.error", {"code": "retrieval_embedding_failed", "message": str(error), "recoverable": True})

        now = datetime.now(timezone.utc)
        ranked = []
        for record in candidates.values():
            memory_tokens = {token for token in re.findall(r"[\w']+", record.content.casefold()) if token not in self._QUERY_STOPWORDS}
            overlap_count = len(query_tokens & memory_tokens)
            overlap = overlap_count / max(len(query_tokens | memory_tokens), 1)
            semantic = max(semantic_scores.get(record.id, 0.0), 0.0)
            keyword_score = keyword_scores.get(record.id, 0.0)
            try:
                age_days = max((now - datetime.fromisoformat(record.updated_at.replace("Z", "+00:00"))).total_seconds() / 86400, 0)
            except ValueError:
                age_days = 365
            recency = math.exp(-age_days / 365)
            type_boost = 1.0 if record.type in {MemoryType.EXPLICIT, MemoryType.PROJECT, MemoryType.PREFERENCE, MemoryType.PERSONAL_FACT} else 0.5
            score = semantic * 0.52 + keyword_score * 0.18 + overlap * 0.12 + record.importance * 0.1 + recency * 0.04 + type_boost * 0.04
            short_non_recall = len(query_tokens) <= 6 and not recall_intent
            strong_short_semantic = len(query_tokens) >= 4 and semantic >= 0.82
            if short_non_recall and overlap_count == 0 and not strong_short_semantic:
                continue
            # A literal subject match is strong evidence even when the compact
            # embedding backend assigns a modest cosine score.  Semantic-only
            # matches remain deliberately stricter so greetings cannot pull in
            # unrelated memories.
            threshold = 0.30 if overlap_count > 0 else (0.48 if recall_intent else 0.52)
            semantic_only_allowed = (not short_non_recall and semantic >= 0.62) or strong_short_semantic
            if score >= threshold and (overlap_count > 0 or keyword_score >= 0.75 or semantic_only_allowed or recall_intent):
                record.score = round(score, 6)
                ranked.append(record)
        ranked.sort(key=lambda item: item.score or 0, reverse=True)
        selected = ranked[:min(max(limit, 1), 10)]
        self.repository.mark_accessed([record.id for record in selected])
        if selected:
            await self.emit(
                "isaac.memory.recalled",
                {"count": len(selected), "ids": [record.id for record in selected], "types": [record.type for record in selected]},
            )
        return selected

    async def handle_directive(self, message: str, preferred_memory_id: str | None = None) -> tuple[bool, str, MemoryRecord | None]:
        directive = parse_directive(message)
        if not directive:
            return False, "", None
        if directive.secret_reason:
            return True, "I won't store that as memory because it appears to contain a secret. Secrets belong in secure credential storage.", None
        if not self.repository:
            await self.emit("isaac.memory.error", {"code": "memory_unavailable", "message": self.error or "Memory unavailable", "recoverable": True})
            return True, "My memory service is temporarily unavailable, so I didn't save that. We can still keep talking.", None
        if directive.action == "remember":
            record = await self.create(
                memory_type=directive.memory_type,
                content=directive.content,
                source="user_explicit",
                confidence=1.0,
                importance=1.0,
            )
            return True, "Got it. I'll remember that.", record
        if directive.action == "update":
            matches = await self.search(directive.query, limit=3)
            supersedes = matches[0].id if matches and (matches[0].score or 0) >= 0.4 else None
            record = await self.create(
                memory_type=directive.memory_type,
                content=directive.content,
                source="user_update",
                confidence=0.95,
                importance=0.9,
                supersedes_id=supersedes,
            )
            return True, "Understood. I've updated that memory.", record
        if directive.action == "forget":
            if directive.query.casefold() in {"that", "it", "this"}:
                if not preferred_memory_id:
                    return True, "Which memory do you want me to forget?", None
                try:
                    deleted = self.repository.delete(preferred_memory_id)
                except KeyError:
                    return True, "That memory is already gone.", None
                await self.emit("isaac.memory.deleted", {"id": deleted.id, "type": deleted.type})
                await self.remove_summary_fact(deleted.content)
                return True, "Done. I forgot that.", deleted
            matches = await self.search(directive.query, limit=5)
            if not matches:
                return True, "I couldn't find an active memory matching that.", None
            query_tokens = set(re.findall(r"[a-z0-9]+", directive.query.casefold())) - self._QUERY_STOPWORDS
            def forget_rank(record: MemoryRecord) -> tuple[int, int, float]:
                subject_tokens = {
                    token
                    for tag in record.tags if tag.startswith("subject:")
                    for token in re.findall(r"[a-z0-9]+", tag.removeprefix("subject:"))
                }
                return (len(query_tokens & subject_tokens), record.type != MemoryType.CONVERSATION_SUMMARY, record.score or 0)
            matches.sort(key=forget_rank, reverse=True)
            first_rank = forget_rank(matches[0])
            second_rank = forget_rank(matches[1]) if len(matches) > 1 else (-1, -1, -1.0)
            if len(matches) > 1 and first_rank[0] == second_rank[0] and first_rank[1] == second_rank[1] and first_rank[2] - second_rank[2] < 0.08:
                return True, "I found more than one possible memory. Please be more specific so I don't delete the wrong one.", None
            deleted = self.repository.delete(matches[0].id)
            await self.emit("isaac.memory.deleted", {"id": deleted.id, "type": deleted.type})
            await self.remove_summary_fact(deleted.content)
            return True, "Done. I forgot that.", deleted
        return False, "", None

    async def update(self, memory_id: str, changes: dict[str, Any]) -> MemoryRecord:
        if not self.repository:
            raise RuntimeError("Memory service is unavailable")
        if "content" in changes and secret_reason(str(changes["content"])):
            raise ValueError("Refusing to store content that appears to contain a secret")
        record = self.repository.update(memory_id, **changes)
        if "content" in changes:
            await self._embed_record(record)
        await self.emit("isaac.memory.updated", {"id": record.id, "type": record.type})
        return record

    async def delete(self, memory_id: str) -> MemoryRecord:
        if not self.repository:
            raise RuntimeError("Memory service is unavailable")
        record = self.repository.delete(memory_id)
        await self.emit("isaac.memory.deleted", {"id": record.id, "type": record.type})
        await self.remove_summary_fact(record.content)
        return record

    async def refresh_summary_fact(self, old_content: str, new_content: str) -> None:
        if not self.repository or old_content == new_content:
            return
        for summary in self.list(status="active", memory_type=MemoryType.CONVERSATION_SUMMARY, limit=500):
            if summary.source == "automatic_session_summary" and old_content in summary.content:
                await self.update(summary.id, {"content": summary.content.replace(old_content, new_content)})

    async def remove_summary_fact(self, content: str) -> None:
        if not self.repository:
            return
        for summary in self.list(status="active", memory_type=MemoryType.CONVERSATION_SUMMARY, limit=500):
            if summary.source != "automatic_session_summary" or content not in summary.content:
                continue
            remaining = re.sub(r"\s+", " ", summary.content.replace(content, "")).strip()
            if remaining in {"", "Session continuity:"}:
                deleted = self.repository.delete(summary.id, "source_memory_forgotten")
                await self.emit("isaac.memory.deleted", {"id": deleted.id, "type": deleted.type})
            else:
                await self.update(summary.id, {"content": remaining})

    def list(self, **filters: Any) -> list[MemoryRecord]:
        if not self.repository:
            return []
        return self.repository.list(**filters)

    def get(self, memory_id: str) -> MemoryRecord:
        if not self.repository:
            raise RuntimeError("Memory service is unavailable")
        return self.repository.get(memory_id, include_inactive=True)

    def close(self) -> None:
        if self.repository:
            self.repository.close()
