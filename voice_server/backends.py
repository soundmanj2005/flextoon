"""Speech backends for the FlexToon voice server.

A backend turns text (and optionally a reference recording of the voice to clone)
into WAV bytes. To add another engine (for example Qwen3-TTS), write a class with a
`synthesize` method and register it in `make_backend`.
"""
from __future__ import annotations

import io
import math
import re
import struct
import wave
from pathlib import Path


def split_sentences(text: str, max_chars: int = 300) -> list[str]:
    """Split on sentence ends, then pack sentences into chunks of at most max_chars."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    chunks, current = [], ""
    for s in sentences:
        while len(s) > max_chars:  # a single very long sentence: break at a space
            cut = s.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                chunks.append(current)
                current = ""
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if current and len(current) + 1 + len(s) > max_chars:
            chunks.append(current)
            current = s
        else:
            current = f"{current} {s}".strip()
    if current:
        chunks.append(current)
    return chunks


class DummyBackend:
    """Test backend: a quiet tone whose length matches the text. Needs no model or GPU."""
    name = "dummy"

    def synthesize(self, text: str, reference: Path | None, options: dict) -> bytes:
        seconds = max(1.0, len(text.split()) / 2.5)
        rate = 24000
        frames = b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(seconds * rate))
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)
        return buf.getvalue()


class ChatterboxBackend:
    """Chatterbox (Resemble AI, MIT). Zero-shot cloning from a short reference clip.

    NOTE: the project's README says it is retired and no longer maintained. It still
    works, but expect no updates. Output carries an inaudible PerTh watermark.
    """
    name = "chatterbox"

    def __init__(self, device: str | None = None):
        self.device = device
        self.model = None

    def _load(self):
        if self.model is None:
            import torch
            from chatterbox.tts import ChatterboxTTS
            device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            print(f"Loading Chatterbox on {device} (first run downloads the model)...", flush=True)
            self.model = ChatterboxTTS.from_pretrained(device=device)
        return self.model

    def synthesize(self, text: str, reference: Path | None, options: dict) -> bytes:
        import torch
        import torchaudio as ta
        model = self._load()
        kwargs = {k: float(options[k]) for k in ("exaggeration", "cfg_weight") if k in options}
        if reference:
            kwargs["audio_prompt_path"] = str(reference)
        pause = torch.zeros(1, int(model.sr * 0.15))
        parts = []
        for chunk in split_sentences(text):
            parts += [model.generate(chunk, **kwargs).cpu(), pause]
        wav = torch.cat(parts[:-1], dim=-1)
        buf = io.BytesIO()
        ta.save(buf, wav, model.sr, format="wav")
        return buf.getvalue()


def make_backend(name: str):
    name = (name or "chatterbox").lower()
    if name == "dummy":
        return DummyBackend()
    if name == "chatterbox":
        return ChatterboxBackend()
    raise ValueError(f"Unknown VOICE_BACKEND {name!r}. Use 'chatterbox' or 'dummy'.")
