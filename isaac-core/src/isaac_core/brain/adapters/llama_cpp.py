from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from isaac_core.brain.base import BrainAdapter, DeltaCallback
from isaac_core.brain.settings import BrainSettings


class BrainUnavailableError(RuntimeError):
    pass


class GenerationCancelled(RuntimeError):
    pass


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class LlamaCppAdapter(BrainAdapter):
    def __init__(self, settings: BrainSettings) -> None:
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self.api_key = secrets.token_urlsafe(32)
        self.state = "disabled" if not settings.enabled else "not_loaded"
        self.error: str | None = None
        self.load_seconds: float | None = None
        self._active_responses: dict[str, Any] = {}
        self._log_file = None
        self._job_handle = None

    @property
    def base_url(self) -> str:
        return f"http://{self.settings.host}:{self.settings.port}"

    def _request(self, path: str, *, data: bytes | None = None, timeout: float = 2):
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return urlopen(Request(self.base_url + path, data=data, headers=headers), timeout=timeout)

    async def start(self) -> None:
        if not self.settings.enabled:
            return
        if self.process and self.process.returncode is None:
            return
        self.state = "loading"
        self.error = None
        if self.settings.adapter != "llama_cpp":
            self.state = "error"
            self.error = f"Unsupported Brain adapter: {self.settings.adapter}"
            raise BrainUnavailableError(self.error)
        if not self.settings.runtime_path.is_file():
            self.state = "error"
            self.error = f"llama.cpp runtime not found: {self.settings.runtime_path}"
            raise BrainUnavailableError(self.error)
        if not self.settings.model_path.is_file():
            self.state = "error"
            self.error = f"Model not found: {self.settings.model_path}"
            raise BrainUnavailableError(self.error)
        log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ISAAC" / "Logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = (log_dir / "llama-server.log").open("a", encoding="utf-8")
        args = [
            str(self.settings.runtime_path), "-m", str(self.settings.model_path),
            "--host", self.settings.host, "--port", str(self.settings.port),
            "--no-webui",
            "-ngl", str(self.settings.gpu_layers), "-c", str(self.settings.context_size),
            "-t", str(self.settings.threads), "-tb", str(self.settings.batch_threads),
            "-np", str(self.settings.parallel), "-fa", self.settings.flash_attention,
        ]
        creationflags = 0x08000000 if os.name == "nt" else 0
        started = time.perf_counter()
        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=self._log_file,
            stderr=self._log_file,
            creationflags=creationflags,
            env={**os.environ, "LLAMA_API_KEY": self.api_key},
        )
        if os.name == "nt":
            try:
                self._assign_kill_on_close_job(self.process.pid)
            except Exception as error:
                self.process.terminate()
                await self.process.wait()
                self.state = "error"
                self.error = str(error)
                raise
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.returncode is not None:
                self.state = "error"
                self.error = f"llama-server exited with code {self.process.returncode}"
                raise BrainUnavailableError(self.error)
            try:
                response = await asyncio.to_thread(self._request, "/health")
                with response:
                    payload = json.loads(response.read())
                if payload.get("status") == "ok":
                    self.load_seconds = time.perf_counter() - started
                    self.state = "ready"
                    return
            except (OSError, URLError, TimeoutError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.25)
        self.state = "error"
        self.error = "llama-server model load timed out"
        await self.stop()
        raise BrainUnavailableError(self.error)

    async def stop(self) -> None:
        for response in tuple(self._active_responses.values()):
            try:
                response.close()
            except Exception:
                pass
        self._active_responses.clear()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=8)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self._job_handle:
            ctypes.windll.kernel32.CloseHandle(self._job_handle)
            self._job_handle = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        if self.state != "error":
            self.state = "stopped"

    def _assign_kill_on_close_job(self, pid: int) -> None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.OpenProcess.restype = ctypes.c_void_p
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise BrainUnavailableError("Unable to create llama.cpp lifecycle job")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise BrainUnavailableError("Unable to configure llama.cpp lifecycle job")
        process_handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process_handle:
            kernel32.CloseHandle(job)
            raise BrainUnavailableError("Unable to open llama.cpp process for lifecycle supervision")
        try:
            if not kernel32.AssignProcessToJobObject(job, process_handle):
                kernel32.CloseHandle(job)
                raise BrainUnavailableError("Unable to attach llama.cpp to lifecycle supervision")
        finally:
            kernel32.CloseHandle(process_handle)
        self._job_handle = job

    async def cancel(self, request_id: str) -> None:
        response = self._active_responses.get(request_id)
        if response:
            await asyncio.to_thread(self._abort_response, response)

    @staticmethod
    def _abort_response(response: Any) -> None:
        """Interrupt a blocking streamed read before closing its buffered wrapper."""
        try:
            raw_socket = response.fp.raw._sock
            raw_socket.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        response.close()

    async def generate(
        self,
        request_id: str,
        messages: list[dict[str, str]],
        on_delta: DeltaCallback,
        cancel_event: asyncio.Event,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.state != "ready":
            raise BrainUnavailableError(f"Brain is not loaded (state: {self.state})")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        generation = {
            "model": "isaac-local",
            "messages": messages,
            "stream": True,
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "top_k": self.settings.top_k,
            "min_p": self.settings.min_p,
            "presence_penalty": self.settings.presence_penalty,
        }
        if options:
            allowed = {"max_tokens", "temperature", "top_p", "top_k", "min_p", "presence_penalty"}
            generation.update({key: value for key, value in options.items() if key in allowed})
        body = json.dumps(generation).encode("utf-8")

        def produce() -> None:
            response = None
            try:
                response = self._request("/v1/chat/completions", data=body, timeout=600)
                self._active_responses[request_id] = response
                for raw_line in response:
                    if cancel_event.is_set():
                        raise GenerationCancelled()
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))
                    if chunk.get("timings"):
                        loop.call_soon_threadsafe(queue.put_nowait, ("timings", chunk["timings"]))
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
            except Exception as error:
                kind = "cancelled" if cancel_event.is_set() or isinstance(error, GenerationCancelled) else "error"
                loop.call_soon_threadsafe(queue.put_nowait, (kind, error))
            finally:
                self._active_responses.pop(request_id, None)
                if response:
                    response.close()

        worker = asyncio.create_task(asyncio.to_thread(produce))
        text_parts: list[str] = []
        timings: dict[str, Any] = {}
        try:
            while True:
                kind, value = await queue.get()
                if kind == "delta":
                    text_parts.append(value)
                    await on_delta(value)
                elif kind == "timings":
                    timings = value
                elif kind == "cancelled":
                    raise GenerationCancelled()
                elif kind == "error":
                    raise BrainUnavailableError(str(value)) from value
                elif kind == "done":
                    break
        finally:
            await worker
        return {"text": "".join(text_parts), "timings": timings}

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "adapter": "llama_cpp",
            "model": self.settings.model_path.name,
            "modelPath": str(self.settings.model_path),
            "runtimePath": str(self.settings.runtime_path),
            "pid": self.process.pid if self.process and self.process.returncode is None else None,
            "loadSeconds": self.load_seconds,
            "contextSize": self.settings.context_size,
            "threads": self.settings.threads,
            "gpuLayers": self.settings.gpu_layers,
            "error": self.error,
        }
