from __future__ import annotations

from dataclasses import dataclass
import re

from isaac_core.memory.types import MemoryType


@dataclass
class MemoryDirective:
    action: str
    content: str = ""
    memory_type: str = MemoryType.EXPLICIT
    query: str = ""
    secret_reason: str | None = None


EXPLICIT_PATTERNS = [
    re.compile(r"^\s*(?:isaac[,\s]+)?remember\s+that\s+(.+?)\s*[.!]?\s*$", re.I | re.S),
    re.compile(r"^\s*(?:isaac[,\s]+)?save\s+(?:this|that)\s*(?::|as)?\s+(.+?)\s*[.!]?\s*$", re.I | re.S),
    re.compile(r"^\s*(?:isaac[,\s]+)?don['’]?t\s+forget\s+(?:that\s+)?(.+?)\s*[.!]?\s*$", re.I | re.S),
]
FORGET_PATTERN = re.compile(
    r"^\s*(?:(?:no[,\s]+)?(?:fr|for\s+real|seriously|please)[,\s]+)*(?:isaac[,\s]+)?"
    r"(?:(?:forget|remove)\s+(?:the\s+memory\s+|what\s+i\s+told\s+you\s+)?(?:about\s+)?(.+?)|"
    r"delete\s+(.+?)(?:\s+from\s+(?:your\s+)?memory)?|"
    r"don['’]?t\s+remember\s+(.+?)(?:\s+anymore)?)\s*[.!]?\s*$",
    re.I | re.S,
)
UPDATE_PATTERN = re.compile(r"^\s*(my\s+.+?\s+is)\s+now\s+(.+?)\s*[.!]?\s*$", re.I | re.S)

SECRET_PATTERNS = [
    re.compile(r"\b(?:password|passcode|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|private[_ -]?key|auth(?:entication)?[_ -]?cookie|secret)\b", re.I),
    re.compile(r"\b(?:sk|pk)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
]


def secret_reason(content: str) -> str | None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            return "The requested memory appears to contain a password, token, key, cookie, or other secret."
    return None


def classify(content: str) -> str:
    lowered = content.casefold()
    if any(term in lowered for term in ("project", "monitor", "architecture", "approved", "design", "implementation")):
        return MemoryType.PROJECT
    if any(term in lowered for term in ("i prefer", "i don't like", "i do not like", "always ", "my preferred")):
        return MemoryType.PREFERENCE
    if lowered.startswith(("my ", "i am ", "i have ")):
        return MemoryType.PERSONAL_FACT
    return MemoryType.EXPLICIT


def parse_directive(message: str) -> MemoryDirective | None:
    for pattern in EXPLICIT_PATTERNS:
        match = pattern.match(message)
        if match:
            content = match.group(1).strip().rstrip(".") + "."
            return MemoryDirective(
                action="remember",
                content=content,
                memory_type=classify(content),
                secret_reason=secret_reason(content),
            )
    forget = FORGET_PATTERN.match(message)
    if forget:
        query = next(group for group in forget.groups() if group is not None).strip().rstrip(".")
        query = re.sub(r"\s+from\s+(?:your\s+)?memory$", "", query, flags=re.I).strip()
        return MemoryDirective(action="forget", query=query)
    update = UPDATE_PATTERN.match(message)
    if update:
        content = f"{update.group(1)} {update.group(2)}".strip().rstrip(".") + "."
        return MemoryDirective(
            action="update",
            content=content[0].upper() + content[1:],
            query=update.group(1),
            memory_type=MemoryType.PREFERENCE,
            secret_reason=secret_reason(content),
        )
    return None
