from __future__ import annotations

import asyncio
import unittest

from isaac_core.brain.adapters.llama_cpp import GenerationCancelled
from isaac_core.brain.base import BrainAdapter
from isaac_core.conversations.service import ConversationService
from isaac_core.identity import load_identity


class FastBrain(BrainAdapter):
    async def start(self): pass
    async def stop(self): pass
    async def cancel(self, request_id): pass
    def status(self): return {"state": "ready"}
    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        await on_delta("Okay.")
        return {"text": "Okay.", "timings": {}}


class FakeMemory:
    @staticmethod
    def directive(_message): return None
    async def handle_directive(self, _message, _preferred=None): return False, "", None
    async def search(self, _message, limit=5): return []


class OrderedConsolidator:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.allow_completion = asyncio.Event()
        self.interrupt_count = 0

    async def consolidate(self, conversation_id, user_message, assistant_response, *, research_performed=False):
        self.calls.append(user_message)
        self.started.set()
        try:
            await self.allow_completion.wait()
        except asyncio.CancelledError:
            raise
        if self.cancelled.is_set():
            self.cancelled.clear()
            raise GenerationCancelled()
        return {"analyzed": True, "created": 1}

    async def interrupt(self):
        if not self.started.is_set() or self.allow_completion.is_set():
            return False
        self.interrupt_count += 1
        self.cancelled.set()
        self.allow_completion.set()
        return True


class ConsolidationQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        async def emit(event_type, payload, conversation_id, correlation_id):
            self.events.append((event_type, payload, conversation_id, correlation_id))
        self.service = ConversationService(FastBrain(), load_identity(), emit, FakeMemory())
        self.consolidator = OrderedConsolidator()
        self.service.consolidator = self.consolidator
        self.conversation_id = self.service.create()["conversationId"]

    async def asyncTearDown(self):
        self.consolidator.allow_completion.set()
        await self.service.stop()

    async def send_and_wait(self, message):
        result = await self.service.send(self.conversation_id, message)
        await self.service.conversations[self.conversation_id].active_task
        return result

    async def test_normal_follow_up_does_not_flush_pending_consolidation(self):
        await self.send_and_wait("We decided the latency project will use an ordered queue.")
        self.assertEqual(len(self.service.consolidation_queue), 1)
        started = asyncio.get_running_loop().time()
        await self.send_and_wait("Okay, reply normally.")
        self.assertLess(asyncio.get_running_loop().time() - started, 0.1)
        self.assertEqual(len(self.service.consolidation_queue), 1)

    async def test_jobs_are_processed_in_chronological_order(self):
        first = "We decided the latency project will use an ordered queue."
        second = "We also decided the latency project will prioritize foreground requests."
        await self.send_and_wait(first)
        await self.send_and_wait(second)
        self.consolidator.allow_completion.set()
        await asyncio.wait_for(self.service.consolidation_drained.wait(), 4)
        self.assertEqual(self.consolidator.calls, [first, second])

    async def test_shutdown_retries_an_interrupted_head_job(self):
        message = "We decided the latency project will preserve queued work during shutdown."
        await self.send_and_wait(message)
        await asyncio.wait_for(self.consolidator.started.wait(), 2)
        await self.send_and_wait("Reply normally while consolidation is active.")
        await self.service.stop()
        self.assertEqual(len(self.service.consolidation_queue), 0)
        self.assertEqual(self.consolidator.calls, [message, message])


if __name__ == "__main__":
    unittest.main()
