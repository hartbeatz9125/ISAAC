from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from isaac_core import PROTOCOL_VERSION


class EventBroker:
    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._sequence = 0
        self._lock = asyncio.Lock()

    def add(self, connection: Any) -> None:
        self._clients.add(connection)

    def remove(self, connection: Any) -> None:
        self._clients.discard(connection)

    async def envelope(
        self,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return {
            "id": str(uuid4()),
            "type": event_type,
            "version": PROTOCOL_VERSION,
            "sequence": sequence,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "correlationId": correlation_id,
            "conversationId": conversation_id,
            "payload": payload,
        }

    async def send(
        self,
        connection: Any,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        envelope = await self.envelope(event_type, payload, correlation_id, conversation_id)
        await connection.send(json.dumps(envelope, separators=(",", ":")))

    async def broadcast(
        self,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        if not self._clients:
            return
        message = json.dumps(
            await self.envelope(event_type, payload, correlation_id, conversation_id),
            separators=(",", ":"),
        )
        clients = tuple(self._clients)
        results = await asyncio.gather(
            *(client.send(message) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.remove(client)
