from __future__ import annotations

from isaac_core.brain.adapters.llama_cpp import LlamaCppAdapter
from isaac_core.brain.base import BrainAdapter
from isaac_core.brain.settings import BrainSettings


def create_brain(settings: BrainSettings | None = None) -> BrainAdapter:
    resolved = settings or BrainSettings.from_environment()
    if resolved.adapter == "llama_cpp":
        return LlamaCppAdapter(resolved)
    raise ValueError(f"Unknown Brain adapter: {resolved.adapter}")
