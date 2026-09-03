from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
import wave

import soundfile as sf
from kokoro_onnx import Kokoro


root = Path(os.environ["VOICE_ROOT"])
text = "Alright. I found the problem. It's not as bad as I thought."

started = time.perf_counter()
kokoro = Kokoro(root / "models/kokoro/kokoro-v1.0.onnx", root / "models/kokoro/voices-v1.0.bin")
load_seconds = time.perf_counter() - started
started = time.perf_counter()
samples, sample_rate = kokoro.create(text, voice="am_adam", speed=1.0, lang="en-us")
kokoro_seconds = time.perf_counter() - started
sf.write(root / "output/kokoro-smoke.wav", samples, sample_rate)
kokoro_duration = len(samples) / sample_rate
print(f"KOKORO load={load_seconds:.3f}s gen={kokoro_seconds:.3f}s audio={kokoro_duration:.3f}s realtime={kokoro_duration / kokoro_seconds:.2f}x")

started = time.perf_counter()
subprocess.run(
    [
        root / "venv/Scripts/piper.exe",
        "--model", root / "models/piper/en_US-joe-medium/en_US-joe-medium.onnx",
        "--output_file", root / "output/piper-smoke.wav",
    ],
    input=text,
    text=True,
    check=True,
    capture_output=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
piper_seconds = time.perf_counter() - started
with wave.open(str(root / "output/piper-smoke.wav"), "rb") as audio:
    piper_duration = audio.getnframes() / audio.getframerate()
print(f"PIPER gen={piper_seconds:.3f}s audio={piper_duration:.3f}s realtime={piper_duration / piper_seconds:.2f}x")
