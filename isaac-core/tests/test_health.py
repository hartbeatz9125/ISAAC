from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TOKEN = "phase-one-test-token-123456789"
CORE_ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request_json(port: int, path: str, *, token: str | None = TOKEN, method: str = "GET"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(f"http://127.0.0.1:{port}{path}", headers=headers, method=method)
    with urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read())


class RunningCore:
    def __init__(self) -> None:
        self.port = free_port()
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="isaac-core-test-"))
        env = os.environ.copy()
        env["ISAAC_CORE_TOKEN"] = TOKEN
        env["PYTHONPATH"] = str(CORE_ROOT / "src")
        env["ISAAC_BRAIN_ENABLED"] = "0"
        env["ISAAC_MEMORY_DB"] = str(self.runtime_dir / "isaac.db")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "isaac_core.main", "--port", str(self.port)],
            cwd=CORE_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    async def wait_ready(self) -> None:
        for _ in range(50):
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate()
                raise RuntimeError(f"Core exited early: {stdout} {stderr}")
            try:
                await asyncio.to_thread(request_json, self.port, "/v1/health")
                return
            except Exception:
                await asyncio.sleep(0.1)
        raise TimeoutError("Core did not become ready")

    async def stop(self) -> None:
        if self.process.poll() is None:
            try:
                await asyncio.to_thread(request_json, self.port, "/v1/system/shutdown", method="POST")
            except Exception:
                self.process.terminate()
            await asyncio.to_thread(self.process.wait, 5)
        await asyncio.to_thread(self.process.communicate)


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.core = RunningCore()
        await self.core.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.core.stop()

    async def test_health_and_capabilities_are_versioned(self) -> None:
        status, health = await asyncio.to_thread(request_json, self.core.port, "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["protocolVersion"], 1)
        status, capabilities = await asyncio.to_thread(request_json, self.core.port, "/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(capabilities["protocolVersion"], 1)
        self.assertIn("core.health", capabilities["capabilities"])
        self.assertIn("conversation.streaming", capabilities["capabilities"])
        self.assertEqual(capabilities["brain"]["state"], "disabled")
        self.assertEqual(capabilities["memory"]["state"], "ready")

    async def test_authentication_is_required(self) -> None:
        with self.assertRaises(HTTPError) as context:
            await asyncio.to_thread(request_json, self.core.port, "/v1/health", token=None)
        self.assertEqual(context.exception.code, 401)

    async def test_non_loopback_binding_is_rejected(self) -> None:
        env = os.environ.copy()
        env["ISAAC_CORE_TOKEN"] = TOKEN
        env["PYTHONPATH"] = str(CORE_ROOT / "src")
        env["ISAAC_BRAIN_ENABLED"] = "0"
        env["ISAAC_MEMORY_DB"] = str(self.core.runtime_dir / "invalid-host.db")
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "isaac_core.main", "--host", "0.0.0.0", "--port", str(free_port())],
            cwd=CORE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)


if __name__ == "__main__":
    unittest.main()
