from __future__ import annotations

import asyncio
import unittest

from isaac_core.brain.adapters.llama_cpp import GenerationCancelled
from isaac_core.brain.base import BrainAdapter
from isaac_core.conversations.service import ConversationService
from isaac_core.identity import load_identity


class StreamingBrain(BrainAdapter):
    def __init__(self, responses, pause_after_first=False):
        self.responses = list(responses)
        self.pause_after_first = pause_after_first
        self.first_delta_sent = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self): pass
    async def stop(self): pass
    async def cancel(self, _request_id): self.release.set()
    def status(self): return {"state": "ready"}

    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        text = self.responses.pop(0)
        split = text.find(". ") + 1
        parts = [text] if split <= 0 else [text[:split], text[split:]]
        for index, part in enumerate(parts):
            if cancel_event.is_set():
                raise GenerationCancelled()
            await on_delta(part)
            if index == 0:
                self.first_delta_sent.set()
                if self.pause_after_first:
                    await self.release.wait()
        return {"text": text, "timings": {}}


class LiveStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def make_service(self, brain):
        events = []
        async def emit(event_type, payload, conversation_id, correlation_id):
            events.append((event_type, payload, conversation_id, correlation_id))
        service = ConversationService(brain, load_identity(), emit)
        return service, events, service.create()["conversationId"]

    async def test_first_safe_sentence_is_emitted_before_generation_completes(self):
        brain = StreamingBrain(["First safe sentence. Second safe sentence."], pause_after_first=True)
        service, events, conversation_id = await self.make_service(brain)
        try:
            await service.send(conversation_id, "Give me two harmless sentences.")
            task = service.conversations[conversation_id].active_task
            await asyncio.wait_for(brain.first_delta_sent.wait(), 1)
            self.assertIn("isaac.response.started", [event[0] for event in events])
            self.assertEqual("".join(event[1]["delta"] for event in events if event[0] == "isaac.response.delta"), "First safe sentence.")
            self.assertNotIn("isaac.response.completed", [event[0] for event in events])
            brain.release.set()
            await task
            completed = next(event for event in events if event[0] == "isaac.response.completed")
            streamed = "".join(event[1]["delta"] for event in events if event[0] == "isaac.response.delta")
            self.assertEqual(streamed, "First safe sentence. Second safe sentence.")
            self.assertEqual(completed[1]["content"], streamed)
        finally:
            brain.release.set()
            await service.stop()

    async def test_unsafe_later_sentence_never_streams_and_prefix_is_preserved(self):
        unsafe = "That approach could work. I visited your house last year."
        brain = StreamingBrain([unsafe, unsafe])
        service, events, conversation_id = await self.make_service(brain)
        try:
            await service.send(conversation_id, "What do you think about that approach?")
            await service.conversations[conversation_id].active_task
            streamed = "".join(event[1]["delta"] for event in events if event[0] == "isaac.response.delta")
            completed = next(event for event in events if event[0] == "isaac.response.completed")
            self.assertTrue(streamed.startswith("That approach could work."))
            self.assertNotIn("visited your house", streamed)
            self.assertIn("don't have physical experiences", streamed)
            self.assertEqual(completed[1]["content"], streamed)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()
