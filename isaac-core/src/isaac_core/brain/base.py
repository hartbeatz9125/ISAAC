from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

DeltaCallback = Callable[[str], Awaitable[None]]


class BrainAdapter(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def generate(
        self,
        request_id: str,
        messages: list[dict[str, str]],
        on_delta: DeltaCallback,
        cancel_event: asyncio.Event,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def cancel(self, request_id: str) -> None: ...

    @abstractmethod
    def status(self) -> dict[str, Any]: ...
