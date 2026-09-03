from __future__ import annotations

from datetime import datetime, timezone
import os

from isaac_core import PROTOCOL_VERSION, __version__

CAPABILITIES = [
    "core.health",
    "core.events",
    "core.lifecycle",
    "brain.local",
    "conversation.temporary",
    "conversation.streaming",
    "conversation.cancellation",
    "memory.persistent",
    "memory.hybrid_search",
    "memory.management",
    "web.read_only_research",
]


def health_payload(started_at: datetime) -> dict[str, object]:
    return {
        "status": "healthy",
        "service": "isaac-core",
        "coreVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "pid": os.getpid(),
        "startedAt": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def capabilities_payload() -> dict[str, object]:
    return {
        "coreVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
    }
