from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import threading
import time
import wave
import winsound

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

LEADING_SILENCE_MS = 250


def add_leading_silence(samples: np.ndarray, sample_rate: int, milliseconds: int = LEADING_SILENCE_MS) -> np.ndarray:
    silence_samples = round(sample_rate * milliseconds / 1000)
    silence = np.zeros(silence_samples, dtype=np.float32)
    return np.concatenate((silence, np.asarray(samples, dtype=np.float32)))


def chunks(text: str, limit: int = 420) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    result: list[str] = []
    current = ""
    for sentence in sentences:
        parts = [sentence[index:index + limit] for index in range(0, len(sentence), limit)] or [sentence]
        for part in parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > limit:
                result.append(current)
                current = part
            else:
                current = candidate
    if current:
        result.append(current)
    return result


def amplitude_envelope(samples: np.ndarray, sample_rate: int, step_ms: int = 50) -> list[float]:
    """Bounded RMS envelope aligned to the first audible sample, not the WAV padding."""
    step = max(1, round(sample_rate * step_ms / 1000))
    levels = [float(np.sqrt(np.mean(np.square(samples[i:i + step]))))
              for i in range(0, len(samples), step)]
    peak = max(levels, default=0.0)
    return [round(min(1.0, value / max(peak, 0.001)), 3) for value in levels]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument("--voice", default="bm_lewis")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    text = sys.stdin.read().strip()
    if not text:
        return 2
    started = time.perf_counter()
    kokoro = Kokoro(args.model, args.voices)
    audio_parts = []
    sample_rate = 24000
    for segment in chunks(text):
        samples, sample_rate = kokoro.create(segment, voice=args.voice, speed=1.0, lang="en-gb")
        audio_parts.append(np.asarray(samples, dtype=np.float32))
    samples = np.concatenate(audio_parts) if audio_parts else np.zeros(1, dtype=np.float32)
    envelope = amplitude_envelope(samples, sample_rate)
    samples = add_leading_silence(samples, sample_rate)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, samples, sample_rate)
    generation_ms = round((time.perf_counter() - started) * 1000, 1)
    with wave.open(str(output), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    playback_invoked = threading.Event()
    playback_clock: dict[str, int] = {}

    def play_audio() -> None:
        playback_clock["deviceStartUnixMs"] = round(time.time() * 1000)
        playback_invoked.set()
        winsound.PlaySound(str(output), winsound.SND_FILENAME)

    playback = threading.Thread(target=play_audio, name="isaac-lewis-playback")
    playback.start()
    playback_invoked.wait()
    # The first non-silent sample begins at this exact boundary in the prepared WAV.
    time.sleep(LEADING_SILENCE_MS / 1000)
    audible_started_ms = round(time.time() * 1000)
    print(json.dumps({
        "event": "started",
        "generationMs": generation_ms,
        "audioSeconds": round(duration, 3),
        "leadingSilenceMs": LEADING_SILENCE_MS,
        "deviceStartUnixMs": playback_clock["deviceStartUnixMs"],
        "audibleStartUnixMs": audible_started_ms,
        "amplitudeEnvelope": envelope,
        "envelopeStepMs": 50,
    }), flush=True)
    playback.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
