#!/usr/bin/env python3
"""Turn an uploaded video into a cartoon and choose what happens to the audio.

Audio modes
  keep     use the video's original audio
  replace  drop the original audio and use a generated voice-over (needs --text)
  mix      generated voice-over on top, original audio ducked underneath
  mute     no audio

CLI example
  python cartoonize.py in.mp4 out.mp4 --strength medium --audio replace \
      --text "Here is what I want to say." --voice-config voice.json

Cartoon looks
  Default is an offline "toon shader" (smoothing + flat colour bands + ink outlines).
  It is a stylised filter, not an AI redraw. For a true AI look, point
  external_command at your own GPU model or API script. It is called as
  `<your command with {input} and {output}>` and must write a video to {output}.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

import providers
import render

PRESETS = {
    "light": dict(sigma_s=4, sigma_r=0.12, steps=1, step=24, sat=1.2, edge_low=0.10, edge_high=0.25),
    "medium": dict(sigma_s=6, sigma_r=0.18, steps=2, step=32, sat=1.4, edge_low=0.08, edge_high=0.20),
    "strong": dict(sigma_s=8, sigma_r=0.25, steps=3, step=48, sat=1.6, edge_low=0.06, edge_high=0.16),
}
AUDIO_MODES = ("keep", "replace", "mix", "mute")


def _toon_graph(p: dict, height: int) -> str:
    smooth = ",".join([f"bilateral=sigmaS={p['sigma_s']}:sigmaR={p['sigma_r']}"] * p["steps"])
    s, half = p["step"], p["step"] // 2
    band = f"trunc(val/{s})*{s}+{half}"
    return (
        f"[0:v]scale=-2:{height},split=2[base][edge];"
        f"[base]{smooth},lutrgb=r='{band}':g='{band}':b='{band}',"
        f"eq=saturation={p['sat']}:contrast=1.05,format=gbrp[col];"
        f"[edge]format=gray,gblur=sigma=1.2,"
        f"edgedetect=low={p['edge_low']}:high={p['edge_high']}:mode=wires,negate,format=gbrp[lines];"
        f"[col][lines]blend=all_mode=multiply,format=yuv420p[v]"
    )


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return bool(out)


def cartoonize_video(src: Path, out: Path, strength: str, height: int,
                     external_command: str | None) -> None:
    """Stage A: picture only (audio is handled separately in stage B)."""
    if external_command:
        args = [a.replace("{input}", str(src)).replace("{output}", str(out))
                for a in shlex.split(external_command)]
        render.run(args)
        return
    graph = _toon_graph(PRESETS[strength], height)
    render.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
        "-filter_complex", graph, "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ])


def process(src: Path, dst: Path, *, strength: str = "medium", audio: str = "keep",
            narration: str | None = None, voice_cfg: dict | None = None,
            original_volume: float = 0.25, music: Path | None = None,
            music_volume: float = 0.12, fit: str = "freeze", height: int = 720,
            external_command: str | None = None, workdir: Path | None = None) -> Path:
    if strength not in PRESETS:
        raise ValueError(f"strength must be one of {list(PRESETS)}")
    if audio not in AUDIO_MODES:
        raise ValueError(f"audio must be one of {list(AUDIO_MODES)}")
    if fit not in ("freeze", "trim"):
        raise ValueError("fit must be 'freeze' or 'trim'")
    if audio in ("replace", "mix") and not (narration and narration.strip()):
        raise ValueError(f"audio mode '{audio}' needs narration text")

    src, dst = Path(src), Path(dst)
    work = Path(workdir) if workdir else dst.parent / f".{dst.stem}_work"
    work.mkdir(parents=True, exist_ok=True)

    vid_dur = render.audio_duration(src)  # ffprobe format duration works for any media
    orig_audio = has_audio(src)

    # Stage A: cartoon picture
    toon = work / "toon.mp4"
    cartoonize_video(src, toon, strength, height, external_command)

    # Generated voice-over
    narr_path, narr_dur = None, 0.0
    if audio in ("replace", "mix"):
        narr_path = providers.make_voice(
            {"id": "narration", "narration": narration.strip()},
            voice_cfg or {"provider": "placeholder"}, work / "audio",
        )
        narr_dur = render.audio_duration(narr_path)

    # Final length: freeze the last frame if the voice-over runs longer, or trim it.
    final_dur = vid_dur
    if narr_path and fit == "freeze":
        final_dur = max(vid_dur, narr_dur + 0.3)
    extra = max(0.0, final_dur - vid_dur)

    # Stage B: mux
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(toon), "-i", str(src)]
    idx = 2
    narr_idx = music_idx = None
    if narr_path:
        cmd += ["-i", str(narr_path)]
        narr_idx, idx = idx, idx + 1
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_idx, idx = idx, idx + 1

    fc, sources = [], []
    use_orig = audio in ("keep", "mix") and orig_audio
    if use_orig:
        vol = 1.0 if audio == "keep" else original_volume
        fc.append(f"[1:a]aresample=44100,volume={vol}[orig]")
    if narr_idx is not None:
        fc.append(f"[{narr_idx}:a]aresample=44100[narr]")
    if use_orig and narr_idx is not None:
        # Duck the original under the voice-over.
        fc.append("[narr]asplit=2[narr1][narr2]")
        fc.append("[orig][narr1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck]")
        sources += ["[duck]", "[narr2]"]
    else:
        if use_orig:
            sources.append("[orig]")
        if narr_idx is not None:
            sources.append("[narr]")
    if music_idx is not None and audio != "mute":
        fc.append(f"[{music_idx}:a]aresample=44100,volume={music_volume}[mus]")
        sources.append("[mus]")

    has_a = bool(sources)
    if has_a:
        if len(sources) == 1:
            fc.append(f"{sources[0]}anull[mix]")
        else:
            fc.append(f"{''.join(sources)}amix=inputs={len(sources)}:duration=longest:normalize=0[mix]")
        norm = ",loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100" if (narr_path or music) else ""
        fc.append(f"[mix]apad=whole_dur={final_dur:.3f},atrim=0:{final_dur:.3f}{norm}[aout]")

    if extra > 0:
        fc.append(f"[0:v]tpad=stop_mode=clone:stop_duration={extra:.3f}[vout]")
        vmap, vcodec = "[vout]", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    else:
        vmap, vcodec = "0:v", ["-c:v", "copy"]

    if fc:
        cmd += ["-filter_complex", ";".join(fc)]
    cmd += ["-map", vmap]
    if has_a:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    else:
        cmd += ["-an"]
    cmd += vcodec + ["-t", f"{final_dur:.3f}", "-movflags", "+faststart", str(dst)]
    render.run(cmd)

    notes = []
    if audio in ("keep", "mix") and not orig_audio:
        notes.append("the source video has no audio track")
    if narr_path and fit == "trim" and narr_dur > vid_dur:
        notes.append("the voice-over was longer than the video and was trimmed")
    if notes:
        print("Note: " + "; ".join(notes), file=sys.stderr)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--strength", choices=list(PRESETS), default="medium")
    ap.add_argument("--audio", choices=AUDIO_MODES, default="keep")
    ap.add_argument("--text", help="Voice-over text for replace/mix")
    ap.add_argument("--text-file", help="Read voice-over text from a file")
    ap.add_argument("--voice-config", help='JSON file like {"provider":"elevenlabs","voice_id":"..."}')
    ap.add_argument("--original-volume", type=float, default=0.25, help="Original audio level in mix mode")
    ap.add_argument("--music", help="Optional background music file")
    ap.add_argument("--music-volume", type=float, default=0.12)
    ap.add_argument("--fit", choices=["freeze", "trim"], default="freeze",
                    help="If the voice-over is longer than the video: freeze last frame, or trim audio")
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--external-command", help="Your own cartoonizer, e.g. 'python my_model.py {input} {output}'")
    args = ap.parse_args()

    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    voice_cfg = json.loads(Path(args.voice_config).read_text()) if args.voice_config else None
    out = process(
        Path(args.input), Path(args.output), strength=args.strength, audio=args.audio,
        narration=text, voice_cfg=voice_cfg, original_volume=args.original_volume,
        music=Path(args.music) if args.music else None, music_volume=args.music_volume,
        fit=args.fit, height=args.height, external_command=args.external_command,
    )
    print(f"Done: {out}")


if __name__ == "__main__":
    main()
