from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import time
from urllib.request import Request, urlopen


class LocalEmbeddingService:
    MODEL_ID = "nomic-embed-text-v1.5.Q4_K_M"

    def __init__(self) -> None:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ISAAC"
        self.runtime_path = Path(os.environ.get(
            "ISAAC_LLAMA_SERVER",
            local / "Runtime" / "llama.cpp" / "b10516" / "llama-server.exe",
        ))
        self.model_path = Path(os.environ.get(
            "ISAAC_EMBED_MODEL_PATH",
            local / "Models" / "nomic-embed-text-v1.5" / "nomic-embed-text-v1.5.Q4_K_M.gguf",
        ))
        self.host = "127.0.0.1"
        self.port = int(os.environ.get("ISAAC_EMBED_PORT", "17779"))
        self.threads = int(os.environ.get("ISAAC_EMBED_THREADS", "4"))
        self._lock = asyncio.Lock()

    async def embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        if not texts:
            return []
        if not self.runtime_path.is_file() or not self.model_path.is_file():
            raise RuntimeError("Local embedding runtime or model is unavailable")
        async with self._lock:
            api_key = secrets.token_urlsafe(32)
            log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ISAAC" / "Logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = (log_dir / "embedding.log").open("a", encoding="utf-8")
            creationflags = 0x08000000 if os.name == "nt" else 0
            process = await asyncio.create_subprocess_exec(
                str(self.runtime_path), "-m", str(self.model_path),
                "--host", self.host, "--port", str(self.port), "--embedding", "--pooling", "mean",
                "-ngl", "0", "-c", "2048", "-b", "512", "-ub", "512",
                "-t", str(self.threads), "-tb", str(self.threads), "-np", "1", "--no-webui",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                creationflags=creationflags,
                env={**os.environ, "LLAMA_API_KEY": api_key},
            )
            try:
                await self._wait_ready(process, api_key)
                prefix = "search_query: " if query else "search_document: "
                payload = json.dumps({
                    "model": self.MODEL_ID,
                    "input": [prefix + text for text in texts],
                    "encoding_format": "float",
                }).encode("utf-8")
                response = await asyncio.to_thread(self._request, "/v1/embeddings", api_key, payload, 30)
                vectors = [item["embedding"] for item in sorted(response["data"], key=lambda item: item["index"])]
                return vectors
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                log_file.close()

    async def _wait_ready(self, process: asyncio.subprocess.Process, api_key: str) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.returncode is not None:
                raise RuntimeError(f"Embedding runtime exited with code {process.returncode}")
            try:
                health = await asyncio.to_thread(self._request, "/health", api_key, None, 1)
                if health.get("status") == "ok":
                    return
            except Exception:
                pass
            await asyncio.sleep(0.1)
        raise RuntimeError("Embedding model load timed out")

    def _request(self, path: str, api_key: str, data: bytes | None, timeout: float):
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        with urlopen(Request(f"http://{self.host}:{self.port}{path}", data=data, headers=headers), timeout=timeout) as response:
            return json.loads(response.read())
