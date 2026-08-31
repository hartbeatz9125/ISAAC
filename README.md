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
