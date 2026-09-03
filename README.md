# Stage 1D — layered gaming interface

This is a local Electron application with two independent, frameless display windows.

## Launch

Double-click:

`release\win-unpacked\Stage 1B Future Desktop.exe`

The leftmost detected Windows display receives the approved gaming artwork. The next display receives the approved reactor artwork. Both windows are borderless and fullscreen.

Press **Ctrl+Shift+Q** to close both windows. Press **Ctrl+Shift+R** to reload them.

The desktop shortcut **Stage 1B Future Desktop** launches the same executable. No terminal, PowerShell, command prompt, npm command, or installer is involved.

## Calibration notes

The approved images remain preserved byte-for-byte in `assets/masters`. The left master is now reference-only: the live gaming display uses five independent artwork assets, CSS 3D surfaces, separate HUD regions, procedural atmosphere, foreground fragments, and depth-aware cursor parallax. Press `\` on the left display to toggle the developer-only alignment overlay. The right reactor implementation is unchanged. No live telemetry or Stage 2 functionality is included.

## ISAAC Phase 1 boundary

Electron remains ISAAC Home and owns both display windows. Its main process supervises the separate, localhost-only Python ISAAC Core process; renderers are not given the Core address or authentication token. Core currently provides only authenticated health, capability discovery, lifecycle, and versioned `isaac.ready` / `isaac.error` events. If Core is unavailable or restarting, the existing desktop continues operating normally.

The protocol contracts are in `contracts`, the boundary design is in `docs/architecture`, Home integration is in `home-core`, and the independently testable service is in `isaac-core`.

## ISAAC Phase 2 developer conversation

Press **Ctrl+Shift+K** to open the temporary ISAAC conversation test window. It communicates only through narrow Electron main-process IPC; the local Core credential and llama.cpp endpoint remain unavailable to the renderer. Conversation context is in memory and lasts only for that Core session. This window is deliberately separate from both permanent monitor designs.

Phase 3 adds local persistent memory. Explicitly say **“Remember that …”**, **“Save this: …”**, or **“Don't forget that …”** to store a durable memory. Use a specific **“Forget what I told you about …”** request to remove one. The temporary window's **Memories** button provides minimal inspect, search, edit, and delete controls. Memory is stored in `%LOCALAPPDATA%\ISAAC\Core\isaac.db`, never renderer storage.
