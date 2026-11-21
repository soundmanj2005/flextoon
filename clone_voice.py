#!/usr/bin/env python3
"""Create a cloned voice of YOURSELF from your own recordings (ElevenLabs).

  export ELEVENLABS_API_KEY=...
  python clone_voice.py --name "My voice" --samples rec1.wav rec2.wav --i-confirm-this-is-my-voice

Prints a voice_id to paste into project.json under voice.voice_id.
Only clone a voice you own or have written permission to clone.
"""
import argparse
import os
import sys
from pathlib import Path

import requests


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--samples", nargs="+", required=True, help="Clean recordings of your own voice")
    ap.add_argument("--i-confirm-this-is-my-voice", action="store_true", dest="confirmed")
    args = ap.parse_args()

    if not args.confirmed:
        sys.exit("Refusing to continue: pass --i-confirm-this-is-my-voice to confirm the "
                 "samples are your own voice (or you have the speaker's written permission).")
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Set ELEVENLABS_API_KEY first.")

    files = [("files", (Path(p).name, open(p, "rb"))) for p in args.samples]
    resp = requests.post(
        "https://api.elevenlabs.io/v1/voices/add",
        headers={"xi-api-key": key},
        data={"name": args.name, "description": "Cloned from my own recordings"},
        files=files,
        timeout=300,
    )
    resp.raise_for_status()
    print("voice_id:", resp.json()["voice_id"])


if __name__ == "__main__":
    main()
