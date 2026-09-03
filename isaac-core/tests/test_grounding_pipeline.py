from __future__ import annotations

import unittest

from isaac_core.brain.base import BrainAdapter
from isaac_core.conversations.service import ConversationService
from isaac_core.identity import load_identity


class SequenceBrain(BrainAdapter):
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = []

    async def start(self): pass
    async def stop(self): pass
    async def cancel(self, request_id): pass
    def status(self): return {"state": "ready"}

    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        self.calls.append({"messages": messages, "options": options})
        text = self.drafts.pop(0)
        for chunk in (text[:10], text[10:]):
            if chunk:
                await on_delta(chunk)
        return {"text": text, "timings": {"generationMs": 5}}


class GroundingPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_service(self, drafts, user_message):
        events = []

        async def emit(event_type, payload, conversation_id, correlation_id):
            events.append((event_type, payload))

        brain = SequenceBrain(drafts)
        service = ConversationService(brain, load_identity(), emit)
        conversation_id = service.create()["conversationId"]
        await service.send(conversation_id, user_message)
        await service.conversations[conversation_id].active_task
        return brain, events

    async def test_invalid_draft_is_buffered_and_grounded_regeneration_is_delivered(self):
        invalid = "I left my phone in my pocket."
        valid = "I don't have a physical phone, so I couldn't have put it anywhere."
        brain, events = await self.run_service([invalid, valid], "Where did you put your phone?")
        deltas = "".join(payload["delta"] for event, payload in events if event == "isaac.response.delta")
        self.assertEqual(deltas, valid)
        self.assertNotIn(invalid, deltas)
        statuses = [payload["status"] for event, payload in events if event == "isaac.grounding.validation"]
        self.assertEqual(statuses, ["failed", "regeneration_triggered", "passed"])
        self.assertEqual(len(brain.calls), 2)
        regenerated_messages = brain.calls[1]["messages"]
        self.assertEqual([message["role"] for message in regenerated_messages], ["system", "user"])
        self.assertIn("Grounding correction", regenerated_messages[0]["content"])

    async def test_two_invalid_drafts_use_natural_fallback(self):
        brain, events = await self.run_service(
            ["I left my phone at home.", "My phone is still in my apartment."],
            "Where did you put your phone?",
        )
        completed = [payload for event, payload in events if event == "isaac.response.completed"][0]
        self.assertIn("don't have a physical body or possessions", completed["content"])
        self.assertTrue(completed["timings"]["grounding"]["fallbackUsed"])
        self.assertEqual(len(brain.calls), 2)


if __name__ == "__main__":
    unittest.main()
