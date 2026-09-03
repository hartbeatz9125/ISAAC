# ISAAC Home ↔ Core boundary

Phase 1 introduces an optional, local-only boundary without changing either renderer.

## Ownership

- ISAAC Home owns Electron, monitor windows, renderers, the game carousel, game execution, lighting, theme presentation, and Spotify presentation.
- ISAAC Core owns only protocol health and event transport in Phase 1.
- Future intelligence, identity, memory, permissions, tools, knowledge, voice, vision, and resource management belong in Core, but none are implemented here.

## Transport

- Core binds to `127.0.0.1` only.
- Electron main communicates through authenticated HTTP and WebSocket connections.
- Renderers do not receive the Core URL, access token, client, or events in Phase 1.
- Protocol version `1` is shared by the OpenAPI and JSON Schema contracts under `contracts/`.

## Failure behavior

- Home launches its existing windows immediately and remains usable if Core is missing or unhealthy.
- Home starts Core when no compatible instance answers its authenticated health check.
- Home reconnects with bounded exponential backoff after a WebSocket or process failure.
- Home stops only a Core process that Home started. Independently started Core processes remain independent.

## Phase 1 non-goals

No model, conversation, personality, memory, RAG, tool execution, permission prompts, voice, vision, screen capture, research, or automation is included.

## Phase 2 extension

Core now owns a model-independent Brain interface. Its first `llama_cpp` adapter supervises an authenticated, loopback-only llama.cpp Vulkan process and translates local streaming output into the existing v1 event envelope. The runtime and GGUF model are application data, not source assets.

Temporary conversation sessions and ISAAC identity live in Core. Electron main exposes only narrow developer-window IPC commands and relays sanitized versioned events. Neither permanent monitor renderer receives the Core or llama.cpp credentials, URLs, or sockets. The temporary Phase 2 test window is isolated under `home-core/dev-chat` and can be deleted without changing either monitor.

## Phase 3 memory boundary

Persistent memory is a separate Core service backed by a versioned SQLite database in ISAAC application data. It is independent of identity, temporary conversations, model weights, Brain adapters, future document knowledge, and Home. Validated writes, secret rejection, hybrid retrieval, soft deletion, supersession, audit revisions, and expiration are enforced before data reaches storage.

Only relevant memory content is inserted into a transient Brain prompt. Renderer-facing memory events contain identifiers, types, and counts rather than database internals. The developer window uses narrow main-process commands for explicit inspection and management; it never opens SQLite or stores memory in localStorage.
