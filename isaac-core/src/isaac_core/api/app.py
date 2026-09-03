from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hmac
import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from websockets.datastructures import Headers
from websockets.http11 import Response

from isaac_core import PROTOCOL_VERSION, __version__
from isaac_core.api.health import CAPABILITIES, capabilities_payload, health_payload
from isaac_core.brain.service import create_brain
from isaac_core.conversations.service import ConversationService
from isaac_core.events.broker import EventBroker
from isaac_core.identity import load_identity
from isaac_core.memory.service import MemoryService
from isaac_core.web import WebResearchService


class CoreApplication:
    def __init__(self, token: str) -> None:
        self.token = token
        self.started_at = datetime.now(timezone.utc)
        self.stop_event = asyncio.Event()
        self.broker = EventBroker()
        self.brain = create_brain()
        self.memory = MemoryService.open(self._emit_memory)
        self.web = WebResearchService()
        self.conversations = ConversationService(self.brain, load_identity(), self._emit, self.memory, web=self.web)
        self.brain_start_task: asyncio.Task | None = None

    async def _emit(
        self,
        event_type: str,
        payload: dict[str, object],
        conversation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        await self.broker.broadcast(event_type, payload, correlation_id, conversation_id)

    async def _emit_memory(self, event_type: str, payload: dict[str, object]) -> None:
        await self._emit(event_type, payload)

    async def start_brain(self) -> None:
        try:
            await self.brain.start()
            if self.brain.status().get("state") == "ready":
                await self._emit("isaac.brain.ready", self.brain.status())
        except Exception as error:
            await self._emit(
                "isaac.error",
                {"code": "brain_load_failed", "message": str(error), "recoverable": True},
            )

    async def stop(self) -> None:
        await self.conversations.stop()
        if self.brain_start_task and not self.brain_start_task.done():
            self.brain_start_task.cancel()
            await asyncio.gather(self.brain_start_task, return_exceptions=True)
        await self.brain.stop()
        self.memory.close()

    def _authorized(self, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    @staticmethod
    def _response(status: HTTPStatus, payload: dict[str, object]) -> Response:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return Response(
            status.value,
            status.phrase,
            Headers({"Content-Type": "application/json", "Content-Length": str(len(body))}),
            body,
        )

    async def process_request(self, connection, request):
        parsed = urlsplit(request.path)
        path = parsed.path
        auth_header = request.headers.get("Authorization", "")
        bearer = auth_header[7:] if auth_header.startswith("Bearer ") else None

        if path == "/v1/events":
            query_token = parse_qs(parsed.query).get("access_token", [None])[0]
            if not self._authorized(query_token):
                return self._response(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            if request.headers.get("Upgrade", "").lower() != "websocket":
                return self._response(HTTPStatus.UPGRADE_REQUIRED, {"error": "websocket required"})
            return None

        if path not in {"/v1/health", "/v1/capabilities", "/v1/system/shutdown"}:
            return self._response(HTTPStatus.NOT_FOUND, {"error": "not found"})
        if not self._authorized(bearer):
            return self._response(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})

        if path == "/v1/health":
            payload = health_payload(self.started_at)
            payload["brain"] = self.brain.status()
            payload["memory"] = self.memory.status()
            return self._response(HTTPStatus.OK, payload)
        if path == "/v1/capabilities":
            payload = capabilities_payload()
            payload["brain"] = self.brain.status()
            payload["memory"] = self.memory.status()
            return self._response(HTTPStatus.OK, payload)
        if request.method != "POST":
            return self._response(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "POST required"})
        asyncio.get_running_loop().call_soon(self.stop_event.set)
        return self._response(HTTPStatus.ACCEPTED, {"status": "stopping"})

    async def websocket_handler(self, connection) -> None:
        self.broker.add(connection)
        try:
            await self.broker.send(
                connection,
                "isaac.ready",
                {
                    "coreVersion": __version__,
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": CAPABILITIES,
                    "brain": self.brain.status(),
                    "memory": self.memory.status(),
                },
            )
            brain_status = self.brain.status()
            if brain_status.get("state") == "error":
                await self.broker.send(
                    connection,
                    "isaac.error",
                    {
                        "code": "brain_load_failed",
                        "message": brain_status.get("error") or "Local model failed to load",
                        "recoverable": True,
                    },
                )
            async for message in connection:
                await self._handle_command(connection, message)
        finally:
            self.broker.remove(connection)

    async def _handle_command(self, connection, raw_message: str) -> None:
        request_id = None
        try:
            command = json.loads(raw_message)
            request_id = command.get("requestId")
            command_type = command.get("type")
            payload = command.get("payload") or {}
            if not request_id or not command_type:
                raise ValueError("Command type and requestId are required")
            if command_type == "conversation.create":
                result = self.conversations.create()
            elif command_type == "conversation.message":
                result = await self.conversations.send(payload.get("conversationId", ""), payload.get("content", ""))
            elif command_type == "conversation.cancel":
                result = await self.conversations.cancel(payload.get("conversationId", ""))
            elif command_type == "brain.status":
                result = self.brain.status()
            elif command_type == "memory.status":
                result = self.memory.status()
            elif command_type == "memory.list":
                result = {"memories": [record.public() for record in self.memory.list(
                    status=payload.get("status", "active"),
                    memory_type=payload.get("type"),
                    limit=payload.get("limit", 100),
                )]}
            elif command_type == "memory.search":
                result = {"memories": [record.public() for record in await self.memory.search(
                    payload.get("query", ""), payload.get("limit", 10)
                )]}
            elif command_type == "memory.get":
                result = self.memory.get(payload.get("id", "")).public()
            elif command_type == "memory.create":
                record = await self.memory.create(
                    memory_type=payload.get("type", "explicit"), content=payload.get("content", ""),
                    source="developer_api", confidence=payload.get("confidence", 1.0),
                    importance=payload.get("importance", 0.9), tags=payload.get("tags", []),
                    related_project=payload.get("relatedProject"), expires_at=payload.get("expiresAt"),
                )
                result = record.public()
            elif command_type == "memory.update":
                result = (await self.memory.update(payload.get("id", ""), payload.get("changes", {}))).public()
            elif command_type == "memory.delete":
                result = (await self.memory.delete(payload.get("id", ""))).public()
            else:
                raise ValueError(f"Unsupported command: {command_type}")
            await self.broker.send(
                connection,
                "isaac.command.result",
                {"requestId": request_id, "ok": True, "result": result},
                request_id,
                result.get("conversationId") if isinstance(result, dict) else None,
            )
        except Exception as error:
            await self.broker.send(
                connection,
                "isaac.command.result",
                {"requestId": request_id, "ok": False, "error": str(error)},
                request_id,
            )
