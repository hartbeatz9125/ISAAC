# ISAAC Core (Phase 1)

This is the independently testable, localhost-only ISAAC Core boundary. Phase 1 contains lifecycle, health, capability discovery, and authenticated events only. It contains no model, memory, tools, voice, vision, or automation.

From this directory in PowerShell:

```powershell
$env:PYTHONPATH = "src"
$env:ISAAC_CORE_TOKEN = "replace-with-a-long-random-local-token"
python -m isaac_core.main
```

Run tests from the repository root:

```powershell
$env:PYTHONPATH = "isaac-core/src"
python -m unittest discover -s isaac-core/tests -v
```

The service defaults to `127.0.0.1:17777`. Every endpoint, including the WebSocket event stream, requires the per-launch bearer token.

## Phase 2 local Brain

The first adapter supervises the official llama.cpp Vulkan server at `127.0.0.1:17778`, authenticated with a different ephemeral key held only in Core. Defaults are centralized in `brain/settings.py` and can be overridden with `ISAAC_BRAIN_*`, `ISAAC_LLAMA_SERVER`, and `ISAAC_MODEL_PATH` environment variables. The model and runtime live under `%LOCALAPPDATA%\ISAAC`, outside this repository.

Identity v1 is stored separately in `identity/isaac-v1.md`. Conversations are temporary in-memory sessions; Phase 2 creates no permanent conversation or memory database.

## Phase 3 persistent memory

Structured long-term memory uses a versioned SQLite database at `%LOCALAPPDATA%\ISAAC\Core\isaac.db`. Migration SQL is source-controlled under `memory/migrations`, while the database, WAL, and indexes remain local runtime state outside Git.

Retrieval combines SQLite FTS5, exact token overlap, type, importance, recency, access history, and on-demand CPU embeddings from Nomic Embed Text v1.5 Q4_K_M. The embedding process is stopped after each batch. Memory failure is recoverable and never prevents ordinary local conversation.
