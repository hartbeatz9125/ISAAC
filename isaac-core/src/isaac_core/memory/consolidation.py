from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from isaac_core.brain.adapters.llama_cpp import GenerationCancelled
from isaac_core.brain.base import BrainAdapter
from isaac_core.memory.policy import secret_reason
from isaac_core.memory.service import MemoryService
from isaac_core.memory.types import MemoryRecord, MemoryType


ConsolidationEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
_TRIVIAL = re.compile(r"^\s*(?:yo|hey|hi|hello|lol|lmao|ok|okay|cool|nice|thanks|thank you|that'?s crazy|what'?s up)[.!?\s]*$", re.I)
_MEANINGFUL = re.compile(
    r"\b(?:actually|agreed|build(?:ing)?|changed?|correct(?:ion|ed)?|decid(?:e|ed|ing)|discovered?|fix(?:ed)?|goal|important|instead|next step|ongoing|plan(?:ning|ned)?|prefer|project|resolved?|roadmap|should use|switch(?:ed|ing)? to|using|will use)\b",
    re.I,
)
_QUESTION_ONLY = re.compile(r"^\s*(?:who|what|when|where|why|how|can|could|should|would|do|does|did|is|are)\b.*\?\s*$", re.I | re.S)
_ALLOWED_TYPES = {
    MemoryType.PREFERENCE,
    MemoryType.PROJECT,
    MemoryType.PERSONAL_FACT,
    MemoryType.SYSTEM_ISAAC,
}


@dataclass(frozen=True)
class ConsolidationCandidate:
    memory_type: str
    subject: str
    content: str
    confidence: float
    importance: float
    related_project: str | None = None
    is_correction: bool = False


def should_analyze(user_message: str, *, research_performed: bool = False) -> bool:
    text = user_message.strip()
    if not text or len(text) < 24 or _TRIVIAL.match(text):
        return False
    if _QUESTION_ONLY.match(text):
        return False
    return bool(_MEANINGFUL.search(text) or (research_performed and re.search(r"\b(?:project|decision|goal|plan|investigate|research)\b", text, re.I)))


class MemoryConsolidator:
    def __init__(self, brain: BrainAdapter, memory: MemoryService, emit: ConsolidationEvent) -> None:
        self.brain = brain
        self.memory = memory
        self.emit = emit
        self.session_items: dict[str, list[str]] = {}
        self.active_request_id: str | None = None
        self.active_cancel_event: asyncio.Event | None = None

    async def consolidate(self, conversation_id: str, user_message: str, assistant_response: str, *, research_performed: bool = False) -> dict[str, Any]:
        if not should_analyze(user_message, research_performed=research_performed):
            return {"analyzed": False, "created": 0, "updated": 0}
        started = time.perf_counter()
        request_id = "memory-consolidation-" + str(uuid4())
        cancel_event = asyncio.Event()
        self.active_request_id = request_id
        self.active_cancel_event = cancel_event
        try:
            candidates = await self._extract(user_message, assistant_response, research_performed, request_id, cancel_event)
        finally:
            if self.active_request_id == request_id:
                self.active_request_id = None
                self.active_cancel_event = None
        if cancel_event.is_set():
            raise GenerationCancelled()
        created = updated = skipped = 0
        accepted: list[str] = []
        for candidate in candidates[:3]:
            if cancel_event.is_set():
                raise GenerationCancelled()
            action, record = await self._store(candidate)
            if action == "created": created += 1
            elif action == "updated": updated += 1
            else: skipped += 1
            if record and action in {"created", "updated", "existing"}:
                accepted.append(record.content)
        if accepted:
            session = self.session_items.setdefault(conversation_id, [])
            for content in accepted:
                if content not in session:
                    session.append(content)
            if len(session) >= 2:
                await self._upsert_summary(conversation_id, session[-6:])
        elapsed = (time.perf_counter() - started) * 1000
        await self.emit("isaac.memory.consolidated", {"created": created, "updated": updated, "skipped": skipped, "elapsedMs": round(elapsed, 1)})
        return {"analyzed": True, "created": created, "updated": updated, "skipped": skipped, "elapsedMs": elapsed}

    async def interrupt(self) -> bool:
        request_id = self.active_request_id
        cancel_event = self.active_cancel_event
        if not request_id or not cancel_event:
            return False
        cancel_event.set()
        await self.brain.cancel(request_id)
        return True

    async def _extract(
        self,
        user_message: str,
        assistant_response: str,
        research_performed: bool,
        request_id: str,
        cancel_event: asyncio.Event,
    ) -> list[ConsolidationCandidate]:
        system = (
            "You are a conservative memory-consolidation extractor, not a conversational assistant. Return JSON only. "
            "Extract at most 3 durable facts useful in future conversations: stable user preferences/goals, committed decisions, ongoing project state, discoveries, unresolved problems, or agreed next steps. "
            "Reject greetings, jokes, moods, questions, speculation, transient details, and anything uncertain. Never store secrets. Never invent. "
            "Do not store webpage facts. Facts directly stated by Kenneth remain eligible even when research_performed is true; the assistant response is withheld in that case. You may also store a real ISAAC research activity event when it is important to an ongoing project. "
            "Use existing types preference, project, personal_fact, or system_isaac. subject must be a stable short key such as weather-station:controller so later corrections use the same key. "
            "Set is_correction true only when Kenneth clearly corrects or replaces an earlier fact or decision. "
            "JSON schema: {\"memories\":[{\"type\":\"project\",\"subject\":\"...\",\"content\":\"one concise standalone factual sentence\",\"confidence\":0.0,\"importance\":0.0,\"related_project\":null,\"is_correction\":false}]}. /no_think"
        )
        payload = {
            "user_message": user_message[:4000],
            "assistant_response": "" if research_performed else assistant_response[:4000],
            "research_performed": research_performed,
        }
        result = await self.brain.generate(
            request_id,
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            lambda _delta: asyncio.sleep(0),
            cancel_event,
            {"temperature": 0, "top_p": 1, "top_k": 1, "max_tokens": 350, "presence_penalty": 0},
        )
        try:
            match = re.search(r"\{.*\}", result.get("text", ""), re.S)
            parsed = json.loads(match.group(0) if match else "{}")
        except (ValueError, TypeError, json.JSONDecodeError):
            return []
        candidates = []
        for item in parsed.get("memories", []) if isinstance(parsed, dict) else []:
            try:
                memory_type = MemoryType(str(item.get("type", "")))
                subject = re.sub(r"[^a-z0-9:_-]+", "-", str(item.get("subject", "")).casefold()).strip("-")[:80]
                content = re.sub(r"\s+", " ", str(item.get("content", "")).strip())[:1000]
                confidence = min(max(float(item.get("confidence", 0)), 0), 0.95)
                importance = min(max(float(item.get("importance", 0)), 0), 0.9)
            except (ValueError, TypeError):
                continue
            if memory_type not in _ALLOWED_TYPES or not subject or len(content) < 15 or confidence < 0.72 or importance < 0.5 or secret_reason(content):
                continue
            subject = self._canonical_subject(subject, content)
            related_project = subject.split(":", 1)[0] if memory_type == MemoryType.PROJECT and ":" in subject else str(item.get("related_project") or "")[:120] or None
            candidates.append(ConsolidationCandidate(memory_type, subject, content, confidence, importance, related_project, item.get("is_correction") is True))
        return candidates

    async def _store(self, candidate: ConsolidationCandidate) -> tuple[str, MemoryRecord | None]:
        subject_tag = "subject:" + candidate.subject
        active = self.memory.list(status="active", limit=500)
        existing = next((record for record in active if subject_tag in record.tags), None)
        if not existing:
            candidate_tokens = self._tokens(candidate.content)
            for record in active:
                if record.type != candidate.memory_type:
                    continue
                record_tokens = self._tokens(record.content)
                similarity = len(candidate_tokens & record_tokens) / max(len(candidate_tokens | record_tokens), 1)
                if similarity >= 0.72:
                    existing = record
                    break
        if existing:
            if existing.source == "user_explicit" and not candidate.is_correction:
                return "skipped_explicit", existing
            if self.memory.repository.normalize(existing.content) == self.memory.repository.normalize(candidate.content):
                return "existing", existing
            updated = await self.memory.update(existing.id, {
                "content": candidate.content,
                "type": candidate.memory_type,
                "confidence": candidate.confidence,
                "importance": candidate.importance,
                "tags": sorted(set(existing.tags + ["auto_consolidated", subject_tag])),
                "related_project": candidate.related_project,
            })
            await self.memory.refresh_summary_fact(existing.content, candidate.content)
            return "updated", updated
        created = await self.memory.create(
            memory_type=candidate.memory_type,
            content=candidate.content,
            source="automatic_consolidation",
            confidence=candidate.confidence,
            importance=candidate.importance,
            tags=["auto_consolidated", subject_tag],
            related_project=candidate.related_project,
        )
        return "created", created

    async def _upsert_summary(self, conversation_id: str, items: list[str]) -> None:
        subject_tag = "session:" + conversation_id
        content = "Session continuity: " + " ".join(items)
        existing = next((record for record in self.memory.list(status="active", memory_type=MemoryType.CONVERSATION_SUMMARY, limit=500) if subject_tag in record.tags), None)
        if existing:
            if existing.content != content:
                await self.memory.update(existing.id, {"content": content, "importance": 0.65, "confidence": 0.85, "tags": ["auto_summary", subject_tag]})
            return
        await self.memory.create(
            memory_type=MemoryType.CONVERSATION_SUMMARY,
            content=content,
            source="automatic_session_summary",
            confidence=0.85,
            importance=0.65,
            tags=["auto_summary", subject_tag],
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.casefold()) if len(token) > 2 and token not in {"the", "and", "for", "that", "with", "kenneth", "isaac"}}

    @staticmethod
    def _canonical_subject(subject: str, content: str) -> str:
        """Do not trust a model-supplied facet when the fact itself identifies one."""
        text = content.casefold()
        facets = (
            (r"\b(?:controller|esp32|raspberry pi|microcontroller)\b", "controller"),
            (r"\b(?:enclosure|waterproof|housing|case)\b", "enclosure"),
            (r"\b(?:upload|sync|every \d+ (?:minute|hour)|interval|schedule)\b", "upload-schedule"),
            (r"\b(?:deadline|due date|ship date)\b", "deadline"),
            (r"\b(?:database|sqlite|postgres|mysql)\b", "database"),
            (r"\b(?:framework|library|runtime)\b", "technology"),
        )
        facet = next((name for pattern, name in facets if re.search(pattern, text)), None)
        if not facet:
            return subject
        parts = subject.split(":")
        base = ":".join(parts[:-1]) if len(parts) > 1 else parts[0]
        return f"{base}:{facet}"[:80]
