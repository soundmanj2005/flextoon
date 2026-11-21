"""Voice and image providers.

Each provider is a small function so you can swap services without touching
the renderer. Results are cached on disk by a hash of the inputs, so re-running
the pipeline never pays for the same audio or image twice (use --force to
regenerate).
"""
from __future__ import annotations

import base64
import hashlib
import os
import subprocess
from pathlib import Path

import requests


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def apply_pronunciations(text: str, mapping: dict | None) -> str:
    """Respell hard words before sending text to the voice model."""
    for word, respelling in (mapping or {}).items():
        text = text.replace(word, respelling)
    return text


# --------------------------------------------------------------------------
# Voice
# --------------------------------------------------------------------------

def make_voice(scene: dict, cfg: dict, out_dir: Path, force: bool = False) -> Path:
    provider = cfg.get("provider", "placeholder")
    text = apply_pronunciations(scene["narration"], cfg.get("pronunciations"))

    if provider == "local_file":
        # Use audio you recorded or generated elsewhere: <folder>/<scene id>.<ext>
        folder = Path(cfg["folder"])
        for ext in ("mp3", "wav", "m4a", "flac", "ogg"):
            candidate = folder / f"{scene['id']}.{ext}"
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"No audio file for scene {scene['id']} in {folder}")

    key = _hash(provider, cfg.get("voice_id", ""), cfg.get("model_id", ""), text)
    ext = "mp3" if provider == "elevenlabs" else "wav"
    out = out_dir / f"{scene['id']}_{key}.{ext}"
    if out.exists() and not force:
        return out
    out_dir.mkdir(parents=True, exist_ok=True)

    if provider == "placeholder":
        # Quiet tone whose length matches a ~150 words-per-minute read.
        words = max(1, len(text.split()))
        seconds = max(1.5, words / 2.5)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds:.2f}",
                "-af", "volume=0.03", "-ar", "44100", "-ac", "2", str(out),
            ],
            check=True,
        )
        return out

    if provider == "elevenlabs":
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("Set ELEVENLABS_API_KEY in your environment.")
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['voice_id']}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": cfg.get("model_id", "eleven_multilingual_v2"),
                "voice_settings": cfg.get(
                    "voice_settings", {"stability": 0.5, "similarity_boost": 0.8}
                ),
            },
            timeout=180,
        )
        resp.raise_for_status()
        out.write_bytes(resp.content)
        return out

    raise ValueError(f"Unknown voice provider: {provider}")


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def make_image(scene: dict, cfg: dict, project: dict, out_dir: Path, force: bool = False) -> Path:
    provider = cfg.get("provider", "placeholder")

    # A scene can always point at an image you made yourself.
    if scene.get("image_file"):
        return Path(scene["image_file"])

    if provider == "folder":
        folder = Path(cfg["folder"])
        for ext in ("png", "jpg", "jpeg", "webp"):
            candidate = folder / f"{scene['id']}.{ext}"
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"No image for scene {scene['id']} in {folder}")

    # A scene can name a character made with characters.py; its sheet is the visual reference.
    char, refs = None, []
    if scene.get("character"):
        import characters
        char = characters.load(scene["character"], Path(project.get("characters_dir", "characters")))
        refs = [Path(char["sheet"])]

    prompt = " ".join(
        p for p in (
            project.get("style_prompt", ""),
            char["description"] if char else project.get("character_description", ""),
            scene.get("image_prompt", ""),
        ) if p
    )
    ref_key = "".join(hashlib.sha1(r.read_bytes()).hexdigest()[:8] for r in refs)
    key = _hash(provider, cfg.get("model", ""), prompt, ref_key)
    out = out_dir / f"{scene['id']}_{key}.png"
    if out.exists() and not force:
        return out
    out_dir.mkdir(parents=True, exist_ok=True)

    if provider == "placeholder":
        _placeholder_image(scene, prompt, key, out)
        return out

    if provider == "openai":
        if refs:
            prompt = ("Using the character shown in the reference sheet, keep the face, hair, "
                      "outfit and colours exactly consistent. Draw this scene: " + prompt)
        out.write_bytes(openai_image(prompt, cfg, refs))
        return out

    raise ValueError(f"Unknown image provider: {provider}")


def openai_image(prompt: str, cfg: dict, refs: list[Path] | None = None) -> bytes:
    """Text-to-image, or image-edit with reference images when refs are given."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in your environment.")
    headers = {"Authorization": f"Bearer {api_key}"}
    model, size = cfg.get("model", "gpt-image-1"), cfg.get("size", "1536x1024")
    if refs:
        files = [("image[]", (Path(r).name, Path(r).read_bytes(), "image/png" if str(r).endswith(".png") else "image/jpeg"))
                 for r in refs]
        resp = requests.post("https://api.openai.com/v1/images/edits", headers=headers,
                             data={"model": model, "prompt": prompt, "size": size},
                             files=files, timeout=300)
    else:
        resp = requests.post("https://api.openai.com/v1/images/generations", headers=headers,
                             json={"model": model, "prompt": prompt, "size": size}, timeout=300)
    resp.raise_for_status()
    return base64.b64decode(resp.json()["data"][0]["b64_json"])


def _placeholder_image(scene: dict, prompt: str, key: str, out: Path) -> None:
    """Offline stand-in so you can test timing, motion and captions for free."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1920, 1080
    seed = int(key, 16)
    top = (40 + seed % 80, 60 + (seed >> 8) % 80, 110 + (seed >> 16) % 100)
    bottom = (top[2] // 2, top[0] // 2 + 40, top[1] // 2 + 60)
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        px.line([(0, y), (w, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
        small = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = small = ImageFont.load_default()
    px.text((90, 90), f"Scene {scene['id']}", fill="white", font=font)
    y = 200
    words, line = prompt.split(), ""
    for word in words:
        if len(line) + len(word) > 60:
            px.text((90, y), line, fill=(230, 230, 230), font=small)
            y, line = y + 50, ""
        line += word + " "
    px.text((90, y), line, fill=(230, 230, 230), font=small)
    img.save(out)
