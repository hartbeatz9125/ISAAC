from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

from isaac_core.memory.repository import MemoryRepository
from isaac_core.memory.service import MemoryService


class FakeEmbedder:
    MODEL_ID = "test-embedding-v1"

    async def embed(self, texts, *, query):
        vectors = []
        categories = [
            {"right", "monitor", "screen", "presence", "live"},
            {"color", "ultraviolet", "blue", "red"},
            {"editor", "alphaedit", "betaedit", "cursor"},
            {"gpu", "radeon", "graphics"},
        ]
        for text in texts:
            lowered = set(text.casefold().replace("'", "").replace(".", "").split())
            vector = [float(len(lowered & words)) for words in categories] + [0.05]
            length = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / length for value in vector])
        return vectors


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.events = []

        async def emit(event_type, payload):
            self.events.append((event_type, payload))

        self.repository = MemoryRepository(Path(self.temp.name) / "isaac.db")
        self.memory = MemoryService(self.repository, FakeEmbedder(), emit)

    async def asyncTearDown(self):
        self.memory.close()
        self.temp.cleanup()

    async def test_migration_and_structured_explicit_memory(self):
        handled, response, record = await self.memory.handle_directive(
            "Remember that my favorite test color is ultraviolet."
        )
        self.assertTrue(handled)
        self.assertIn("remember", response.lower())
        self.assertEqual(record.type, "personal_fact")
        self.assertEqual(self.repository.connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0], 1)
        recalled = await self.memory.search("What test color did I tell you to remember?")
        self.assertEqual(recalled[0].id, record.id)

    async def test_semantic_recall_filters_irrelevant_memories(self):
        screen = await self.memory.create(memory_type="project", content="The right monitor is ISAAC's main visual presence.", source="test")
        await self.memory.create(memory_type="explicit", content="The favorite test color is ultraviolet.", source="test")
        await self.memory.create(memory_type="preference", content="My test editor is AlphaEdit.", source="test")
        recalled = await self.memory.search("Which screen are you mainly supposed to live on?", limit=5)
        self.assertEqual(recalled[0].id, screen.id)
        self.assertNotIn("ultraviolet", " ".join(record.content for record in recalled).lower())

    async def test_update_supersedes_old_memory(self):
        old = await self.memory.create(memory_type="preference", content="My test editor is AlphaEdit.", source="test")
        handled, response, new = await self.memory.handle_directive("My test editor is now BetaEdit.")
        self.assertTrue(handled)
        self.assertIn("updated", response.lower())
        self.assertEqual(new.supersedes_id, old.id)
        self.assertEqual(self.memory.get(old.id).status, "superseded")
        active = self.memory.list()
        self.assertEqual([record.content for record in active], ["My test editor is BetaEdit."])

    async def test_forget_soft_deletes_one_memory(self):
        record = await self.memory.create(memory_type="explicit", content="The unique test codeword is Marigold.", source="test")
        handled, response, deleted = await self.memory.handle_directive("Forget what I told you about the unique test codeword.")
        self.assertTrue(handled)
        self.assertEqual(deleted.id, record.id)
        self.assertEqual(self.memory.get(record.id).status, "deleted")
        self.assertEqual(self.memory.list(), [])

    async def test_secret_is_rejected(self):
        handled, response, record = await self.memory.handle_directive("Remember that my password is fake-password-123.")
        self.assertTrue(handled)
        self.assertIsNone(record)
        self.assertIn("secret", response.lower())
        self.assertEqual(self.memory.list(), [])

    async def test_temporary_memory_expires(self):
        record = await self.memory.create(
            memory_type="temporary",
            content="This temporary marker has already expired.",
            source="test",
            expires_at="2000-01-01T00:00:00Z",
        )
        self.assertEqual(record.status, "active")
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.memory.get(record.id).status, "expired")

    async def test_natural_forget_soft_deletes_arbitrary_memory(self):
        handled, _response, record = await self.memory.handle_directive("Remember that my test animal is a red panda.")
        self.assertTrue(handled)
        handled, response, deleted = await self.memory.handle_directive("No fr forget my test animal.")
        self.assertTrue(handled)
        self.assertEqual(deleted.id, record.id)
        self.assertEqual(deleted.status, "deleted")
        self.assertIn("forgot", response.lower())
        self.assertEqual(await self.memory.search("What's my test animal?"), [])

    async def test_forget_that_requires_conservative_preferred_memory(self):
        record = await self.memory.create(memory_type="explicit", content="My arbitrary marker is Cedar Finch.", source="test")
        handled, response, deleted = await self.memory.handle_directive("Delete that from your memory.", record.id)
        self.assertTrue(handled)
        self.assertEqual(deleted.id, record.id)
        self.assertEqual(deleted.status, "deleted")
        handled, response, deleted = await self.memory.handle_directive("Forget that.")
        self.assertTrue(handled)
        self.assertIsNone(deleted)
        self.assertIn("which memory", response.lower())

    async def test_short_casual_query_does_not_retrieve_semantically_unrelated_memory(self):
        await self.memory.create(memory_type="personal_fact", content="My test phrase is Purple Waffle.", source="test")
        self.assertEqual(await self.memory.search("Yo bro"), [])
        self.assertEqual(await self.memory.search("I'm bored."), [])
        self.assertEqual(await self.memory.search("What are you thinking about?"), [])

    async def test_direct_recall_still_retrieves_relevant_memory(self):
        record = await self.memory.create(memory_type="personal_fact", content="My test phrase is Purple Waffle.", source="test")
        recalled = await self.memory.search("What's my test phrase?")
        self.assertEqual(recalled[0].id, record.id)


if __name__ == "__main__":
    unittest.main()
