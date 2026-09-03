from __future__ import annotations

from dataclasses import dataclass
import os


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class CoreConfig:
    host: str = "127.0.0.1"
    port: int = 17777
    token: str = ""
    parent_pid: int | None = None

    def validate(self) -> None:
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError("ISAAC Core may bind only to a loopback address")
        if not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if len(self.token) < 16:
            raise ValueError("ISAAC_CORE_TOKEN must contain at least 16 characters")

    @classmethod
    def from_values(
        cls, host: str, port: int, token: str | None, parent_pid: int | None
    ) -> "CoreConfig":
        config = cls(
            host=host,
            port=port,
            token=token or os.environ.get("ISAAC_CORE_TOKEN", ""),
            parent_pid=parent_pid,
        )
        config.validate()
        return config
