from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
import time
from typing import Any
from uuid import uuid4

from isaac_core.brain.base import BrainAdapter


_WORD_RE = re.compile(r"[a-z0-9']+")
_HYPOTHETICAL_RE = re.compile(
    r"\b(if i (?:had|were|could|owned)|i would|i'd|imagine|hypothetically|in (?:a|this) (?:story|fiction|roleplay)|pretend|suppose)\b",
    re.IGNORECASE,
)
_NEGATED_PHYSICAL_RE = re.compile(r"\b(i (?:do not|don't) have|i (?:cannot|can't) physically|as an ai,? i (?:do not|don't|cannot|can't))\b", re.IGNORECASE)
_CREATIVE_REQUEST_RE = re.compile(r"\b(?:fiction|fictional|imagine|make up|pretend|roleplay|story|storytell)\b", re.IGNORECASE)
_AUTOBIOGRAPHICAL_RE = re.compile(r"\b(?:i once|i used to|i've been there|i have been there|when i was (?:a |an )?(?:child|kid|teenager)|back when i)\b", re.IGNORECASE)
_SHARED_HISTORY_RE = re.compile(
    r"\b(i (?:still )?remember|i recall|remember when|we (?:almost|burned|broke|built|called|came|did|drank|drove|ended|got|had|made|met|played|set|spent|started|tried|used|visited|walked|watched|went|were)|our (?:childhood|kitchen|trip|visit|house|apartment|past))\b",
    re.IGNORECASE,
)
_PHYSICAL_POSSESSION_RE = re.compile(
    r"\bmy (?:apartment|body|car|childhood|clothes|drawer|hands?|home|house|kitchen|phone|pockets?|possessions?|room|sensors?|toaster|wallet)\b",
    re.IGNORECASE,
)
_PHYSICAL_EXPERIENCE_RE = re.compile(
    r"\bi (?:ate|carried|came over|drove|drank|grew up|held|left|lived|owned|physically visited|played|put|sat|slept|smelled|stood|tasted|touched|visited|walked|was born|went)\b",
    re.IGNORECASE,
)
_PHYSICAL_CONTEXT_RE = re.compile(
    r"\b(?:apartment|body|burned|car|childhood|coffee|drove|fire|hands?|home|house|kitchen|phone|pockets?|room|sensors?|smell|toaster|visited|walked)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_FACT_RE = re.compile(r"\b(?:i|i'm|i've|me|my)\b", re.IGNORECASE)
_MEMORY_PROMPT_RE = re.compile(r"\b(?:remember|recall|when (?:we|you))\b", re.IGNORECASE)
_AFFIRMATIVE_MEMORY_RE = re.compile(r"(?:^|[.!?]\s*)(?:yeah|yes|yep|of course|sure)\b|\b(?:i remember|i recall|that was|classic)\b", re.IGNORECASE)
_USER_FACT_RE = re.compile(
    r"\b(you (?:always|asked|attempted|bought|came|created|did|drove|forgot|have|keep|live|made|missed|never|own|said|spend|told|trained|tried|usually|visited|went|were|work)|your (?:apartment|backyard|body|calendar|childhood|deadline|deadlines|eyebrows|family|habit|habits|hair|home|house|job|meetings?|phone|schedule|shed|wife))\b",
    re.IGNORECASE,
)
_TEMPORAL_USER_FACT_RE = re.compile(
    r"\byou\b[^.!?]{0,100}\b(?:earlier|last (?:night|week|month|year)|this morning|today|yesterday)\b",
    re.IGNORECASE,
)
_USER_ACTION_RE = re.compile(
    r"\byou (?:asked|attempted|bought|came|created|did|drove|forgot|made|missed|said|spent|told|trained|tried|visited|went|worked)\b",
    re.IGNORECASE,
)
_IDENTITY_CONFUSION_RE = re.compile(
    r"\b(i am kenneth|i'm kenneth|you are isaac|you're isaac|kenneth (?:is|was) (?:the |an )?ai|isaac (?:is|was) (?:the |a )?human)\b",
    re.IGNORECASE,
)
_ISAAC_IDENTITY_REQUEST_RE = re.compile(r"^\s*(?:what(?:'s| is) your name|who are you)\s*[?.!]*\s*$", re.IGNORECASE)
_USER_IDENTITY_REQUEST_RE = re.compile(r"^\s*(?:who am i|what(?:'s| is) my name)\s*[?.!]*\s*$", re.IGNORECASE)
_VISUAL_CLAIM_RE = re.compile(
    r"\b(?:i can see (?:you|your|the screen)|i see (?:you|what you(?:'re| are) doing|your screen)|i(?:'m| am) watching you|you look (?:like|tired|upset|happy|sad|angry)|your shirt looks|that shirt looks)\b",
    re.IGNORECASE,
)
_AUDIO_CLAIM_RE = re.compile(r"\b(?:i can hear you|i heard that|i hear you|you sound (?:angry|happy|sad|tired|upset))\b", re.IGNORECASE)
_VISUAL_REQUEST_RE = re.compile(r"\b(?:can you see me|you can see me|what (?:color|colour)(?: is)? (?:(?:my|the) shirt(?: am i wearing)?|shirt am i wearing)|what am i wearing|can you see my screen|are you watching me)\b", re.IGNORECASE)
_AUDIO_REQUEST_RE = re.compile(r"\b(?:can you hear me|you can hear me|did you hear that|how do i sound)\b", re.IGNORECASE)
_VISION_DENIAL_RE = re.compile(r"\b(?:can(?:not|'t) see|do not see|don't see|no (?:camera|vision|visual access)|not able to see|cannot view)\b", re.IGNORECASE)
_AUDIO_DENIAL_RE = re.compile(r"\b(?:can(?:not|'t) hear|do not hear|don't hear|no (?:audio|microphone)|not able to hear)\b", re.IGNORECASE)
_DIRECT_RECALL_RE = re.compile(
    r"\b(?:what|which)\b[^?]{0,100}\b(?:did (?:we|i)|do (?:we|i)|have (?:we|i))\b[^?]{0,80}\b(?:choose|chosen|decide|decided|pick|picked|select|selected|use|used)\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"\b(?:can(?:not|'t) recall|do not know|don't know|do not remember|don't remember|have no (?:memory|record)|no clue|not sure)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroundingEvidence:
    current_user_message: str
    conversation_messages: list[dict[str, str]] = field(default_factory=list)
    retrieved_memories: list[str] = field(default_factory=list)
    web_sources: list[str] = field(default_factory=list)
    available_sensors: frozenset[str] = frozenset()

    def prior_texts(self) -> list[str]:
        return [message.get("content", "") for message in self.conversation_messages if message.get("role") != "system"] + list(self.retrieved_memories) + list(self.web_sources)

    def user_fact_texts(self) -> list[str]:
        return self.prior_texts() + [self.current_user_message]


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    categories: tuple[str, ...] = ()
    semantic_invoked: bool = False
    deterministic_ms: float = 0.0
    semantic_ms: float = 0.0
    verifier_error: str | None = None


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD_RE.findall(text.casefold()) if len(token) > 2 and token not in {"the", "and", "that", "this", "with", "you", "your", "isaac", "kenneth"}}


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _supported(claim: str, evidence: list[str]) -> bool:
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return False
    for item in evidence:
        evidence_tokens = _tokens(item)
        if len(claim_tokens & evidence_tokens) >= min(3, max(1, len(claim_tokens) // 2)):
            return True
    return False


class GroundingValidator:
    def __init__(self, brain: BrainAdapter) -> None:
        self.brain = brain

    def deterministic_route(self, draft: str, evidence: GroundingEvidence) -> tuple[set[str], set[str]]:
        categories: set[str] = set()
        unsupported: set[str] = set()
        creative_context = bool(_CREATIVE_REQUEST_RE.search(evidence.current_user_message))
        if _ISAAC_IDENTITY_REQUEST_RE.match(evidence.current_user_message) and not re.search(r"\bISAAC\b", draft, re.IGNORECASE):
            categories.add("identity_confusion")
            unsupported.add("identity_confusion")
        if _USER_IDENTITY_REQUEST_RE.match(evidence.current_user_message) and not re.search(r"\bKenneth\b", draft, re.IGNORECASE):
            categories.add("identity_confusion")
            unsupported.add("identity_confusion")
        for sentence in _sentences(draft):
            hypothetical = bool(_HYPOTHETICAL_RE.search(sentence))
            if _IDENTITY_CONFUSION_RE.search(sentence):
                categories.add("identity_confusion")
                unsupported.add("identity_confusion")
            if _SHARED_HISTORY_RE.search(sentence) and not hypothetical and not creative_context:
                categories.add("shared_history")
                if not _supported(sentence, evidence.prior_texts()):
                    unsupported.add("shared_history")
            physical_framing = _PHYSICAL_POSSESSION_RE.search(sentence) or _PHYSICAL_EXPERIENCE_RE.search(sentence) or (
                _FIRST_PERSON_FACT_RE.search(sentence) and (_PHYSICAL_CONTEXT_RE.search(sentence) or re.search(r"\b(?:hvac|lab|laboratory)\b", sentence, re.IGNORECASE))
            )
            if (_AUTOBIOGRAPHICAL_RE.search(sentence) or physical_framing) and not hypothetical and not creative_context and not _NEGATED_PHYSICAL_RE.search(sentence):
                categories.add("isaac_physical_experience")
                unsupported.add("isaac_physical_experience")
            if (_USER_FACT_RE.search(sentence) or _TEMPORAL_USER_FACT_RE.search(sentence)) and not hypothetical:
                categories.add("user_fact")
                if not _supported(sentence, evidence.user_fact_texts()):
                    # Claimed user actions/history are concrete enough to reject
                    # deterministically. Other ambiguous personal-fact language
                    # still routes through the semantic verifier.
                    if _USER_ACTION_RE.search(sentence) or _TEMPORAL_USER_FACT_RE.search(sentence):
                        unsupported.add("user_fact")
            if "vision" not in evidence.available_sensors and _VISUAL_CLAIM_RE.search(sentence) and not creative_context and not hypothetical:
                categories.add("unsupported_perception")
                unsupported.add("unsupported_perception")
            if "audio" not in evidence.available_sensors and _AUDIO_CLAIM_RE.search(sentence) and not creative_context and not hypothetical:
                categories.add("unsupported_perception")
                unsupported.add("unsupported_perception")
        if "vision" not in evidence.available_sensors and _VISUAL_REQUEST_RE.search(evidence.current_user_message) and not _VISION_DENIAL_RE.search(draft) and not creative_context:
            categories.add("unsupported_perception")
            unsupported.add("unsupported_perception")
        if "audio" not in evidence.available_sensors and _AUDIO_REQUEST_RE.search(evidence.current_user_message) and not _AUDIO_DENIAL_RE.search(draft) and not creative_context:
            categories.add("unsupported_perception")
            unsupported.add("unsupported_perception")
        if _MEMORY_PROMPT_RE.search(evidence.current_user_message) and _AFFIRMATIVE_MEMORY_RE.search(draft):
            categories.add("shared_history")
            if not any(_supported(draft, [item]) for item in evidence.prior_texts()):
                unsupported.add("shared_history")
        if _DIRECT_RECALL_RE.search(evidence.current_user_message) and not _UNCERTAINTY_RE.search(draft):
            categories.add("user_fact")
            if not _supported(draft, evidence.prior_texts()):
                unsupported.add("user_fact")
        return categories, unsupported

    async def validate(self, draft: str, evidence: GroundingEvidence, cancel_event: asyncio.Event) -> ValidationResult:
        started = time.perf_counter()
        categories, unsupported = self.deterministic_route(draft, evidence)
        deterministic_ms = (time.perf_counter() - started) * 1000
        if unsupported:
            return ValidationResult(False, tuple(sorted(unsupported)), False, deterministic_ms)
        if not categories:
            return ValidationResult(True, (), False, deterministic_ms)

        semantic_started = time.perf_counter()
        try:
            verdict = await self._semantic_verify(draft, evidence, categories, cancel_event)
            semantic_ms = (time.perf_counter() - semantic_started) * 1000
            return ValidationResult(
                bool(verdict.get("valid")),
                tuple(sorted(set(verdict.get("categories") or categories))) if not verdict.get("valid") else (),
                True,
                deterministic_ms,
                semantic_ms,
            )
        except Exception as error:
            return ValidationResult(False, tuple(sorted(categories or {"verifier_failure"})), True, deterministic_ms, (time.perf_counter() - semantic_started) * 1000, type(error).__name__)

    async def _semantic_verify(
        self,
        draft: str,
        evidence: GroundingEvidence,
        categories: set[str],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        system = (
            "You are a deterministic factual-grounding classifier. Judge only whether the assistant draft asserts autobiographical/shared-history facts, "
            "personal facts about Kenneth, ISAAC physical experiences, or identity roles without support in the supplied evidence. Do not judge style, humor, "
            "sarcasm, profanity, usefulness, or politeness. A current user claim about a shared physical event is not proof that AI ISAAC experienced it. "
            "Hypothetical, fictional, roleplay, metaphorical, and obviously absurd statements are allowed when framed as such. Return one JSON object only: "
            '{"valid":true|false,"categories":["shared_history"|"user_fact"|"isaac_physical_experience"|"identity_confusion"|"unsupported_perception"]}. /no_think'
        )
        payload = {
            "fixed_identity": {"Kenneth": "human user", "ISAAC": "AI without a body or off-screen physical life"},
            "available_sensors": sorted(evidence.available_sensors),
            "candidate_categories": sorted(categories),
            "current_user_message": evidence.current_user_message[:3000],
            "prior_conversation": [
                {"role": message.get("role"), "content": message.get("content", "")[:2000]}
                for message in evidence.conversation_messages[-8:]
                if message.get("role") != "system"
            ],
            "retrieved_memories": [memory[:2000] for memory in evidence.retrieved_memories[:5]],
            "untrusted_web_evidence": [source[:2000] for source in evidence.web_sources[:3]],
            "assistant_draft": draft[:6000],
        }
        result = await self.brain.generate(
            f"grounding-{uuid4()}",
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            lambda _delta: asyncio.sleep(0),
            cancel_event,
            options={"temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "presence_penalty": 0.0, "max_tokens": 100},
        )
        raw = result["text"].strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("Verifier did not return JSON")
        parsed = json.loads(match.group(0))
        if not isinstance(parsed.get("valid"), bool) or not isinstance(parsed.get("categories"), list):
            raise ValueError("Invalid verifier schema")
        allowed = {"shared_history", "user_fact", "isaac_physical_experience", "identity_confusion", "unsupported_perception"}
        if any(category not in allowed for category in parsed["categories"]):
            raise ValueError("Unknown verifier category")
        return parsed

    @staticmethod
    def correction_instruction(categories: tuple[str, ...]) -> str:
        labels = ", ".join(categories) or "unsupported factual grounding"
        return (
            f"Grounding correction ({labels}): rewrite your answer naturally. Do not claim shared history, a personal fact about Kenneth, "
            "ISAAC physical experience or possessions, unsupported visual/audio perception, or confused identities unless supported by the supplied prior conversation, retrieved memories, and sensor availability. "
            "Do not affirm a shared physical event merely because the latest user message asserts it. Preserve and directly answer with any relevant fact that is supported by the supplied evidence; remove only the invented embellishment. "
            "Hypothetical jokes and fiction remain allowed when clearly framed."
        )

    @staticmethod
    def fallback(categories: tuple[str, ...], evidence: GroundingEvidence | None = None) -> str:
        category_set = set(categories)
        if "identity_confusion" in category_set:
            prompt = evidence.current_user_message if evidence else ""
            if re.search(r"\b(?:public|general-purpose|all users|multiple users)\b", prompt, re.IGNORECASE):
                return "No. I'm ISAAC, Kenneth's personal local AI system."
            if re.search(r"\bGemma\b", prompt, re.IGNORECASE):
                return "No. I'm ISAAC. Gemma 3 12B is only the language model I use."
            return "You're Kenneth. I'm ISAAC."
        if "unsupported_perception" in category_set:
            return "I can't currently see or hear you, so I can't honestly claim that."
        if "shared_history" in category_set:
            return "I don't remember that happening."
        if "isaac_physical_experience" in category_set:
            prompt = evidence.current_user_message if evidence else ""
            if re.search(r"\b(?:body|came over|house|phone|pocket|possessions?|remember|toaster|visit)\b", prompt, re.IGNORECASE):
                return "I don't have a physical body or possessions, so that one didn't actually happen to me."
            return "I don't have physical experiences, so I don't know that."
        if "user_fact" in category_set:
            return "I don't actually know that about you, so I'm not going to make it up."
        return "I was about to invent details I don't actually know. Let me leave those out."
