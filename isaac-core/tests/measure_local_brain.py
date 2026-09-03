"""Small, non-stressful local Brain measurement. Not part of unittest discovery."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import time
from uuid import uuid4

from isaac_core.brain.service import create_brain
from isaac_core.identity import load_identity


def process_cpu_seconds(pid: int) -> float:
    if os.name != "nt":
        return 0.0
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return 0.0
    creation = ctypes.c_uint64()
    exit_time = ctypes.c_uint64()
    kernel = ctypes.c_uint64()
    user = ctypes.c_uint64()
    try:
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
            return 0.0
        return (kernel.value + user.value) / 10_000_000
    finally:
        kernel32.CloseHandle(handle)


async def main() -> None:
    brain = create_brain()
    started = time.perf_counter()
    await brain.start()
    loaded = time.perf_counter()
    status = brain.status()
    pid = int(status["pid"])
    first_delta_at = None
    delta_count = 0

    async def on_delta(_delta: str) -> None:
        nonlocal first_delta_at, delta_count
        delta_count += 1
        if first_delta_at is None:
            first_delta_at = time.perf_counter()

    generation_started = time.perf_counter()
    cpu_started = process_cpu_seconds(pid)
    result = await brain.generate(
        str(uuid4()),
        [
            {"role": "system", "content": load_identity()},
            {"role": "user", "content": "In about 180 words, explain why local AI can be useful on a gaming PC. /no_think"},
        ],
        on_delta,
        asyncio.Event(),
    )
    generation_finished = time.perf_counter()
    cpu_finished = process_cpu_seconds(pid)
    elapsed = generation_finished - generation_started
    normalized_cpu = ((cpu_finished - cpu_started) / elapsed / (os.cpu_count() or 1)) * 100
    print(json.dumps({
        "modelLoadSeconds": loaded - started,
        "firstTokenSeconds": (first_delta_at - generation_started) if first_delta_at else None,
        "generationSeconds": elapsed,
        "streamedDeltas": delta_count,
        "processCpuSeconds": cpu_finished - cpu_started,
        "normalizedSystemCpuPercent": normalized_cpu,
        "logicalCpuCount": os.cpu_count(),
        "timings": result.get("timings", {}),
        "responseCharacters": len(result.get("text", "")),
        "status": status,
    }, indent=2))
    await brain.stop()


if __name__ == "__main__":
    asyncio.run(main())
