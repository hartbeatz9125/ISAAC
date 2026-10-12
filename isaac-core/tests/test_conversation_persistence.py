from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest

from isaac_core.brain.base import BrainAdapter
from isaac_core.conversations.repository import ConversationRepository
from isaac_core.conversations.service import ConversationService


class FakeBrain(BrainAdapter):
    async def start(self): pass
    async def stop(self): pass
    async def cancel(self, request_id): pass
    def status(self): return {"state": "ready"}
    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        text = "Stored response."
        await on_delta(text)
        return {"text": text, "timings": {}}


class ConversationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "isaac.db"
        connection = sqlite3.connect(self.path)
        connection.executescript((Path(__file__).parents[1] / "src/isaac_core/memory/migrations/002_conversations.sql").read_text())
        connection.close()
        self.events = []
        async def emit(*event): self.events.append(event)
        self.repo = ConversationRepository(self.path)
        self.service = ConversationService(FakeBrain(), "You are ISAAC.", emit, repository=self.repo)

    async def asyncTearDown(self):
        await self.service.stop()
        self.repo.close()
        self.temp.cleanup()

    async def test_restart_restores_messages_and_last_active_chat(self):
        first = self.service.create()
        await self.service.send(first["conversationId"], "First persistent message")
        await self.service.conversations[first["conversationId"]].active_task
        second = self.service.create()
        self.service.select(first["conversationId"])

        restarted_repo = ConversationRepository(self.path)
        restarted = ConversationService(FakeBrain(), "You are ISAAC.", self.service.emit, repository=restarted_repo)
        try:
            restored = restarted.bootstrap()
            self.assertEqual(restored["conversationId"], first["conversationId"])
            self.assertEqual([m["content"] for m in restored["messages"]], ["First persistent message", "Stored response."])
            self.assertEqual(len(restarted.list()["conversations"]), 2)
            self.assertNotEqual(second["conversationId"], first["conversationId"])
        finally:
            await restarted.stop()
            restarted_repo.close()

    async def test_rename_delete_and_transcripts_do_not_touch_memory_tables(self):
        chat = self.service.create()
        renamed = self.service.rename(chat["conversationId"], "Project Alpha")
        self.assertEqual(renamed["title"], "Project Alpha")
        replacement = self.service.delete(chat["conversationId"])
        self.assertNotEqual(replacement["conversationId"], chat["conversationId"])
        check = sqlite3.connect(self.path)
        try:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            check.close()
        self.assertNotIn("memories", tables)

    async def test_switch_does_not_cross_messages(self):
        one = self.service.create(); two = self.service.create()
        await self.service.send(one["conversationId"], "Only in one")
        await self.service.conversations[one["conversationId"]].active_task
        self.assertEqual(self.service.select(two["conversationId"])["messages"], [])
        self.assertEqual(len(self.service.select(one["conversationId"])["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
