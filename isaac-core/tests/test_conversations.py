from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from isaac_core.brain.base import BrainAdapter
from isaac_core.brain.adapters.llama_cpp import GenerationCancelled
from isaac_core.conversations.service import ConversationService
from isaac_core.identity import load_identity
from isaac_core.web import WebResearchError


class FakeBrain(BrainAdapter):
    def __init__(self) -> None:
        self.ready = True
        self.block = False
        self.seen_messages = []

    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def cancel(self, request_id: str) -> None: pass
    def status(self): return {"state": "ready" if self.ready else "not_loaded"}

    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        self.seen_messages.append(messages)
        if self.block:
            while not cancel_event.is_set():
                await asyncio.sleep(0.01)
            raise GenerationCancelled()
        latest = messages[-1]["content"]
        response = "Your name is Kenneth." if "what is my name" in latest.lower() else "Understood."
        for part in response.split(" "):
            await on_delta(part + " ")
        return {"text": response, "timings": {"predicted_per_second": 42}}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.brain = FakeBrain()
        self.events = []

        async def emit(event_type, payload, conversation_id, correlation_id):
            self.events.append((event_type, payload, conversation_id, correlation_id))

        self.service = ConversationService(self.brain, load_identity(), emit)
        self.conversation_id = self.service.create()["conversationId"]

    async def send_and_wait(self, content: str) -> None:
        await self.service.send(self.conversation_id, content)
        task = self.service.conversations[self.conversation_id].active_task
        await task

    async def test_identity_streaming_and_temporary_context(self) -> None:
        await self.send_and_wait("My name is Kenneth.")
        await self.send_and_wait("What is my name?")
        history = self.brain.seen_messages[-1]
        self.assertIn("You are ISAAC, Kenneth's personal local AI system.", history[0]["content"])
        self.assertIn("read-only web research tool", history[0]["content"])
        self.assertEqual(history[1], {"role": "user", "content": "My name is Kenneth."})
        self.assertEqual(history[2], {"role": "assistant", "content": "Understood."})
        self.assertIn("isaac.response.delta", [event[0] for event in self.events])
        self.assertEqual(history[-1]["content"], "What is my name?")

    async def test_cancellation_is_clean(self) -> None:
        self.brain.block = True
        await self.service.send(self.conversation_id, "Keep talking until cancelled")
        task = self.service.conversations[self.conversation_id].active_task
        result = await self.service.cancel(self.conversation_id)
        self.assertTrue(result["cancelled"])
        await task
        self.assertIn("isaac.response.cancelled", [event[0] for event in self.events])

    async def test_research_failure_is_graceful_and_skips_generation(self) -> None:
        web = AsyncMock()
        web.research.side_effect = WebResearchError("offline")
        service = ConversationService(self.brain, load_identity(), self.service.emit, web=web)
        conversation_id = service.create()["conversationId"]
        await service.send(conversation_id, "Search the internet for a harmless topic")
        await service.conversations[conversation_id].active_task
        completed = [event for event in self.events if event[0] == "isaac.response.completed"][-1]
        self.assertEqual(completed[1]["content"], "I couldn't verify that on the public web right now.")
        self.assertEqual(completed[1]["source"], "web_research_failure")
        self.assertEqual(self.brain.seen_messages, [])


if __name__ == "__main__":
    unittest.main()
