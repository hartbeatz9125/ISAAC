from __future__ import annotations

import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from test_health import RunningCore, TOKEN


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.core = RunningCore()
        await self.core.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.core.stop()

    async def test_ready_uses_versioned_envelope(self) -> None:
        uri = f"ws://127.0.0.1:{self.core.port}/v1/events?access_token={TOKEN}"
        async with connect(uri) as connection:
            import json

            event = json.loads(await connection.recv())
            self.assertEqual(event["type"], "isaac.ready")
            self.assertEqual(event["version"], 1)
            self.assertIsInstance(event["sequence"], int)
            self.assertEqual(event["payload"]["protocolVersion"], 1)

    async def test_event_stream_rejects_wrong_token(self) -> None:
        uri = f"ws://127.0.0.1:{self.core.port}/v1/events?access_token=wrong-token"
        with self.assertRaises(InvalidStatus) as context:
            async with connect(uri):
                pass
        self.assertEqual(context.exception.response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
