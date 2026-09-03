"""Model-independent ISAAC identity loading."""

from pathlib import Path


def load_identity() -> str:
    identity_path = Path(__file__).resolve().parents[3] / "identity" / "isaac-v1.md"
    return identity_path.read_text(encoding="utf-8").strip()
