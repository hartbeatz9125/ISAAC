from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import wave
import winsound


DOCUMENTS_ROOT = Path(os.environ.get("OneDrive", Path.home())) / "Documents" if os.environ.get("OneDrive") else Path.home() / "Documents"
DATA_ROOT = DOCUMENTS_ROOT / "ISAAC Voice Lab Data"
MODEL_ROOT = DATA_ROOT / "models"
OUTPUT_ROOT = DATA_ROOT / "output"
KOKORO_MODEL = MODEL_ROOT / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODEL_ROOT / "kokoro" / "voices-v1.0.bin"
PIPER_ROOT = MODEL_ROOT / "piper"

DEFAULT_TEXT = """Yeah, I'm here. What's up?

Give me a second. I'm looking into it.

Kenneth, I really don't think that's gonna work.

Bro... I don't know what you did, but somehow it actually worked.

Alright. I found the problem. It's not as bad as I thought."""

VOICES = {
    "Kokoro · Adam · American male": ("kokoro", "am_adam"),
    "Kokoro · Michael · American male": ("kokoro", "am_michael"),
    "Kokoro · George · British male": ("kokoro", "bm_george"),
    "Kokoro · Lewis · British male": ("kokoro", "bm_lewis"),
    "Piper · Joe · American male": ("piper", "en_US-joe-medium"),
    "Piper · Norman · American male": ("piper", "en_US-norman-medium"),
}


class VoiceLab(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ISAAC Voice Lab")
        self.geometry("900x720")
        self.minsize(760, 620)
        self.configure(bg="#081019")
        self.busy = False
        self.kokoro = None
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self._build_style()
        self._build_ui()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#081019")
        style.configure("Panel.TFrame", background="#101c28")
        style.configure("TLabel", background="#081019", foreground="#d8efff", font=("Segoe UI", 10))
        style.configure("Title.TLabel", foreground="#5bd5ff", font=("Segoe UI Semibold", 22))
        style.configure("Sub.TLabel", foreground="#88a9bb", font=("Segoe UI", 10))
        style.configure("TButton", background="#12334a", foreground="#e8f8ff", padding=(18, 10), font=("Segoe UI Semibold", 10))
        style.map("TButton", background=[("active", "#17638b"), ("disabled", "#15232d")])
        style.configure("TCombobox", fieldbackground="#0b1620", background="#16364a", foreground="#e8f8ff", padding=8)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=28)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="ISAAC // VOICE LAB", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Local voice audition only · CPU inference · nothing is connected to ISAAC", style="Sub.TLabel").pack(anchor="w", pady=(3, 24))

        ttk.Label(outer, text="VOICE / MODEL").pack(anchor="w")
        self.voice = tk.StringVar(value=next(iter(VOICES)))
        combo = ttk.Combobox(outer, textvariable=self.voice, values=list(VOICES), state="readonly", font=("Segoe UI", 11))
        combo.pack(fill="x", pady=(7, 10))
        combo.bind("<<ComboboxSelected>>", lambda _event: self._voice_changed())

        info = ttk.Frame(outer)
        info.pack(fill="x", pady=(0, 18))
        self.engine_info = ttk.Label(info, text="")
        self.engine_info.pack(side="left")
        ttk.Label(info, text="Speaking rate").pack(side="left", padx=(28, 8))
        self.speed = tk.DoubleVar(value=1.0)
        self.rate = ttk.Scale(info, from_=0.75, to=1.25, variable=self.speed, orient="horizontal", command=self._rate_changed)
        self.rate.pack(side="left", fill="x", expand=True)
        self.rate_value = ttk.Label(info, text="1.00×", width=6)
        self.rate_value.pack(side="left", padx=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 18))
        self.speak_button = ttk.Button(actions, text="▶  SPEAK", command=self.speak)
        self.speak_button.pack(side="left")
        ttk.Button(actions, text="■  STOP", command=self.stop_audio).pack(side="left", padx=10)
        ttk.Button(actions, text="RESET TEST LINES", command=self.reset_text).pack(side="left")
        self.status = ttk.Label(actions, text="Ready", style="Sub.TLabel")
        self.status.pack(side="right")

        ttk.Label(outer, text="TEXT").pack(anchor="w")
        self.text = tk.Text(
            outer, wrap="word", height=18, undo=True, bg="#0b1620", fg="#e8f8ff",
            insertbackground="#5bd5ff", selectbackground="#17638b", relief="flat",
            padx=16, pady=14, font=("Segoe UI", 12), spacing1=2, spacing3=4,
        )
        self.text.insert("1.0", DEFAULT_TEXT)
        self.text.pack(fill="both", expand=True, pady=(7, 0))
        self._setup_text_editing()
        self._voice_changed()

    def _setup_text_editing(self) -> None:
        self.edit_menu = tk.Menu(self, tearoff=False)
        self.edit_menu.add_command(label="Cut", command=self._cut)
        self.edit_menu.add_command(label="Copy", command=self._copy)
        self.edit_menu.add_command(label="Paste", command=self._paste)
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Select All", command=self._select_all)
        self.text.bind("<Control-v>", self._paste)
        self.text.bind("<Control-V>", self._paste)
        self.text.bind("<Control-c>", self._copy)
        self.text.bind("<Control-C>", self._copy)
        self.text.bind("<Control-x>", self._cut)
        self.text.bind("<Control-X>", self._cut)
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-A>", self._select_all)
        self.text.bind("<Button-3>", self._show_edit_menu)

    def _paste(self, _event=None):
        try:
            value = self.clipboard_get()
            if self.text.tag_ranges("sel"):
                self.text.delete("sel.first", "sel.last")
            self.text.insert("insert", value)
        except tk.TclError:
            pass
        return "break"

    def _copy(self, _event=None):
        try:
            value = self.text.get("sel.first", "sel.last")
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
        except tk.TclError:
            pass
        return "break"

    def _cut(self, _event=None):
        try:
            self._copy()
            self.text.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def _select_all(self, _event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.mark_set("insert", "1.0")
        self.text.see("insert")
        return "break"

    def _show_edit_menu(self, event) -> None:
        self.text.focus_set()
        self.text.mark_set("insert", f"@{event.x},{event.y}")
        has_selection = bool(self.text.tag_ranges("sel"))
        self.edit_menu.entryconfigure("Cut", state="normal" if has_selection else "disabled")
        self.edit_menu.entryconfigure("Copy", state="normal" if has_selection else "disabled")
        try:
            self.clipboard_get()
            paste_state = "normal"
        except tk.TclError:
            paste_state = "disabled"
        self.edit_menu.entryconfigure("Paste", state=paste_state)
        self.edit_menu.tk_popup(event.x_root, event.y_root)

    def _voice_changed(self) -> None:
        engine, voice = VOICES[self.voice.get()]
        model = "Kokoro-82M v1.0 ONNX" if engine == "kokoro" else voice + ".onnx"
        self.engine_info.configure(text=f"Engine: {engine.title()}   •   Model: {model}   •   Voice: {voice}")

    def _rate_changed(self, _value: str = "") -> None:
        self.rate_value.configure(text=f"{self.speed.get():.2f}×")

    def reset_text(self) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", DEFAULT_TEXT)

    def stop_audio(self) -> None:
        winsound.PlaySound(None, winsound.SND_PURGE)
        self.status.configure(text="Playback stopped")

    def speak(self) -> None:
        if self.busy:
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("ISAAC Voice Lab", "Enter something for the voice to say.")
            return
        self.busy = True
        self.speak_button.state(["disabled"])
        self.status.configure(text="Generating locally…")
        threading.Thread(target=self._generate, args=(text,), daemon=True).start()

    def _generate(self, text: str) -> None:
        started = time.perf_counter()
        output = OUTPUT_ROOT / "audition.wav"
        try:
            engine, voice = VOICES[self.voice.get()]
            speed = float(self.speed.get())
            if engine == "kokoro":
                if not KOKORO_MODEL.exists() or not KOKORO_VOICES.exists():
                    raise FileNotFoundError("Kokoro model files are missing. Run Voice Lab setup again.")
                if self.kokoro is None:
                    from kokoro_onnx import Kokoro
                    self.kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
                import soundfile as sf
                samples, sample_rate = self.kokoro.create(text, voice=voice, speed=speed, lang="en-us")
                sf.write(output, samples, sample_rate)
            else:
                model = PIPER_ROOT / voice / f"{voice}.onnx"
                piper = DATA_ROOT / "venv" / "Scripts" / "piper.exe"
                if not model.exists() or not piper.exists():
                    raise FileNotFoundError("Piper runtime or voice is missing. Run Voice Lab setup again.")
                subprocess.run(
                    [str(piper), "--model", str(model), "--output_file", str(output), "--length_scale", str(1.0 / speed)],
                    input=text, text=True, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            elapsed = time.perf_counter() - started
            with wave.open(str(output), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            realtime = duration / max(elapsed, 0.001)
            winsound.PlaySound(str(output), winsound.SND_FILENAME | winsound.SND_ASYNC)
            self.after(0, lambda: self.status.configure(text=f"Generated {duration:.1f}s in {elapsed:.2f}s · {realtime:.1f}× realtime"))
        except Exception as error:
            self.after(0, lambda: messagebox.showerror("Voice generation failed", str(error)))
            self.after(0, lambda: self.status.configure(text="Generation failed"))
        finally:
            self.busy = False
            self.after(0, lambda: self.speak_button.state(["!disabled"]))


if __name__ == "__main__":
    VoiceLab().mainloop()
