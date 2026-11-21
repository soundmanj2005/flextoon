"""FlexToon voice server: self-hosted text-to-speech over HTTP.

Run it on a machine with a GPU (for example your Unraid box, in Docker) and point
FlexToon at it. Voices are reference recordings: put <name>.wav (10-20 seconds of clean
speech of the voice you want to clone) in the voices folder, then ask for voice "<name>".

Environment
  VOICE_API_KEY   required. Clients must send it as the X-API-Key header.
  VOICE_BACKEND   chatterbox (default) or dummy (test tone, no model needed)
  VOICES_DIR      where reference recordings live (default ./voices)
  MAX_CHARS       longest text accepted per request (default 5000)

Keep this on your local network. Do not expose it to the internet.
"""
from __future__ import annotations

import hmac
import os
import re
import sys
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request

from .backends import make_backend

API_KEY = os.environ.get("VOICE_API_KEY", "")
if not API_KEY:
    sys.exit("Set VOICE_API_KEY (any long random string) before starting the voice server.")

VOICES = Path(os.environ.get("VOICES_DIR", "voices")).resolve()
MAX_CHARS = int(os.environ.get("MAX_CHARS", "5000"))
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

BACKEND = make_backend(os.environ.get("VOICE_BACKEND", "chatterbox"))
GPU_LOCK = threading.Lock()  # one synthesis at a time

app = Flask(__name__)


def authorized() -> bool:
    return hmac.compare_digest(request.headers.get("X-API-Key", ""), API_KEY)


@app.get("/health")
def health():
    return jsonify(ok=True, backend=BACKEND.name)


@app.get("/voices")
def voices():
    if not authorized():
        return jsonify(error="unauthorized"), 401
    names = sorted(p.stem for p in VOICES.glob("*.wav")) if VOICES.exists() else []
    return jsonify(voices=names)


@app.post("/synthesize")
def synthesize():
    if not authorized():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    voice = str(data.get("voice") or "default")
    if not text:
        return jsonify(error="text is required"), 400
    if len(text) > MAX_CHARS:
        return jsonify(error=f"text is limited to {MAX_CHARS} characters"), 400
    if not NAME_RE.fullmatch(voice):
        return jsonify(error="invalid voice name"), 400

    reference = None
    if voice != "default":
        reference = VOICES / f"{voice}.wav"
        if not reference.exists():
            return jsonify(error=f"No reference recording for voice '{voice}'. "
                                 f"Put {voice}.wav in the voices folder."), 404
    options = {k: data[k] for k in ("exaggeration", "cfg_weight")
               if isinstance(data.get(k), (int, float))}
    with GPU_LOCK:
        wav = BACKEND.synthesize(text, reference, options)
    return Response(wav, mimetype="audio/wav")


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
