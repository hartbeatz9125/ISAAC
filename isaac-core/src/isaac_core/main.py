from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import signal
import sys

from websockets.asyncio.server import serve

from isaac_core import PROTOCOL_VERSION, __version__
from isaac_core.api.app import CoreApplication
from isaac_core.config import CoreConfig


def _parent_is_alive(pid: int) -> bool:
    if os.name == "nt":
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def _monitor_parent(pid: int, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(2)
        if not _parent_is_alive(pid):
            stop_event.set()
            return


async def run(config: CoreConfig) -> None:
    application = CoreApplication(config.token)
    loop = asyncio.get_running_loop()

    def request_stop(*_args) -> None:
        loop.call_soon_threadsafe(application.stop_event.set)

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, request_stop)

    parent_task = None
    if config.parent_pid:
        parent_task = asyncio.create_task(_monitor_parent(config.parent_pid, application.stop_event))

    try:
        async with serve(
            application.websocket_handler,
            config.host,
            config.port,
            process_request=application.process_request,
            compression=None,
            max_size=65536,
            max_queue=16,
            ping_interval=20,
            ping_timeout=20,
        ):
            application.brain_start_task = asyncio.create_task(application.start_brain())
            print(
                json.dumps(
                    {
                        "event": "core.listening",
                        "host": config.host,
                        "port": config.port,
                        "coreVersion": __version__,
                        "protocolVersion": PROTOCOL_VERSION,
                        "pid": os.getpid(),
                    }
                ),
                flush=True,
            )
            await application.stop_event.wait()
    finally:
        await application.stop()
        if parent_task:
            parent_task.cancel()
            await asyncio.gather(parent_task, return_exceptions=True)


def parse_args(argv: list[str] | None = None) -> CoreConfig:
    parser = argparse.ArgumentParser(description="ISAAC Core local boundary service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17777)
    parser.add_argument("--token")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    return CoreConfig.from_values(args.host, args.port, args.token, args.parent_pid)


def main() -> int:
    try:
        asyncio.run(run(parse_args()))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"event": "core.error", "message": str(error)}), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
