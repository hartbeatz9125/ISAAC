from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryType(StrEnum):
    EXPLICIT = "explicit"
    PREFERENCE = "preference"
    PROJECT = "project"
    PERSONAL_FACT = "personal_fact"
    CONVERSATION_SUMMARY = "conversation_summary"
    SYSTEM_ISAAC = "system_isaac"
    TEMPORARY = "temporary"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    EXPIRED = "expired"


@dataclass
class MemoryRecord:
    id: str
    type: str
    content: str
    created_at: str
    updated_at: str
    source: str
    confidence: float
    importance: float
    last_accessed: str | None
    access_count: int
    expires_at: str | None
    status: str
    tags: list[str] = field(default_factory=list)
    related_project: str | None = None
    supersedes_id: str | None = None
    score: float | None = None

    def public(self, include_content: bool = True) -> dict[str, Any]:
        result = {
            "id": self.id,
            "type": self.type,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "source": self.source,
            "confidence": self.confidence,
            "importance": self.importance,
            "lastAccessed": self.last_accessed,
            "accessCount": self.access_count,
            "expiresAt": self.expires_at,
            "status": self.status,
            "tags": self.tags,
            "relatedProject": self.related_project,
            "supersedesId": self.supersedes_id,
        }
        if include_content:
            result["content"] = self.content
        if self.score is not None:
            result["score"] = self.score
        return result
