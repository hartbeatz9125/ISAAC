from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _local_app_data() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        root = str(Path.home() / "AppData" / "Local")
    return Path(root)


@dataclass(frozen=True)
class BrainSettings:
    enabled: bool
    adapter: str
    runtime_path: Path
    model_path: Path
    host: str
    port: int
    threads: int
    batch_threads: int
    context_size: int
    gpu_layers: int
    flash_attention: str
    parallel: int
    max_tokens: int
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    presence_penalty: float

    @classmethod
    def from_environment(cls) -> "BrainSettings":
        isaac_root = _local_app_data() / "ISAAC"
        return cls(
            enabled=os.environ.get("ISAAC_BRAIN_ENABLED", "1").lower() not in {"0", "false", "off"},
            adapter=os.environ.get("ISAAC_BRAIN_ADAPTER", "llama_cpp"),
            runtime_path=Path(os.environ.get("ISAAC_LLAMA_SERVER", isaac_root / "Runtime" / "llama.cpp" / "b10516" / "llama-server.exe")),
            model_path=Path(os.environ.get("ISAAC_MODEL_PATH", isaac_root / "Models" / "Gemma-3-12B" / "gemma-3-12b-it-Q4_K_M.gguf")),
            host="127.0.0.1",
            port=int(os.environ.get("ISAAC_BRAIN_PORT", "17778")),
            threads=int(os.environ.get("ISAAC_BRAIN_THREADS", "6")),
            batch_threads=int(os.environ.get("ISAAC_BRAIN_BATCH_THREADS", "6")),
            context_size=int(os.environ.get("ISAAC_BRAIN_CONTEXT", "4096")),
            gpu_layers=int(os.environ.get("ISAAC_BRAIN_GPU_LAYERS", "40")),
            flash_attention=os.environ.get("ISAAC_BRAIN_FLASH_ATTENTION", "on"),
            parallel=int(os.environ.get("ISAAC_BRAIN_PARALLEL", "1")),
            max_tokens=int(os.environ.get("ISAAC_BRAIN_MAX_TOKENS", "512")),
            temperature=float(os.environ.get("ISAAC_BRAIN_TEMPERATURE", "0.7")),
            top_p=float(os.environ.get("ISAAC_BRAIN_TOP_P", "0.8")),
            top_k=int(os.environ.get("ISAAC_BRAIN_TOP_K", "20")),
            min_p=float(os.environ.get("ISAAC_BRAIN_MIN_P", "0")),
            presence_penalty=float(os.environ.get("ISAAC_BRAIN_PRESENCE_PENALTY", "1.5")),
        )
