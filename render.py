"""FFmpeg rendering: motion clips per scene, captions, music, final MP4."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{tail}")


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _motion_filter(motion: str, frames: int, w: int, h: int, fps: int) -> str:
    """zoompan expressions; the input is pre-scaled to 2x so movement stays smooth."""
    n = max(frames, 1)
    centre_y = "ih/2-(ih/zoom/2)"
    if motion == "zoom_out":
        z, x, y = f"1.15-0.15*on/{n}", "iw/2-(iw/zoom/2)", centre_y
    elif motion == "pan_right":
        z, x, y = "1.12", f"(iw-iw/zoom)*on/{n}", centre_y
    elif motion == "pan_left":
        z, x, y = "1.12", f"(iw-iw/zoom)*(1-on/{n})", centre_y
    elif motion == "none":
        z, x, y = "1", "0", "0"
    else:  # zoom_in (default)
        z, x, y = f"1+0.15*on/{n}", "iw/2-(iw/zoom/2)", centre_y
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


def build_clip(image: Path, audio: Path, out: Path, duration: float,
               motion: str, w: int, h: int, fps: int) -> None:
    frames = math.ceil(duration * fps)
    vf = (
        f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
        f"crop={w * 2}:{h * 2},{_motion_filter(motion, frames, w, h, fps)},format=yuv420p[v];"
        f"[1:a]apad,aresample=44100,aformat=channel_layouts=stereo[a]"
    )
    run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(image), "-i", str(audio),
        "-filter_complex", vf, "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(out),
    ])


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def build_srt(scenes: list[dict], max_words: int = 7) -> str:
    """Caption timing is split by word count across each scene's speech.

    This is an approximation. For exact word timing, swap in a forced aligner.
    """
    lines, idx, t0 = [], 1, 0.0
    for sc in scenes:
        words = sc["narration"].split()
        chunks = [words[i:i + max_words] for i in range(0, len(words), max_words)] or [[]]
        total = max(len(words), 1)
        cursor = t0
        for chunk in chunks:
            span = sc["speech_seconds"] * (len(chunk) / total)
            if chunk:
                lines += [str(idx), f"{_ts(cursor)} --> {_ts(cursor + span)}", " ".join(chunk), ""]
                idx += 1
            cursor += span
        t0 += sc["duration"]
    return "\n".join(lines)


def assemble(clips: list[Path], out_dir: Path, final: Path, srt_text: str | None,
             music: Path | None, music_volume: float, caption_style: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    list_file = out_dir / "clips.txt"
    list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    joined = out_dir / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", str(joined)])

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", joined.name]
    vf = None
    if srt_text:
        (out_dir / "captions.srt").write_text(srt_text, encoding="utf-8")
        vf = f"subtitles=captions.srt:force_style='{caption_style}'"

    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
        fc = (
            f"[1:a]volume={music_volume}[m];"
            "[0:a][m]amix=inputs=2:duration=first:dropout_transition=0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[a]"
        )
        if vf:
            fc = f"[0:v]{vf}[v];" + fc
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
    else:
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100"]

    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart", str(final.resolve())]
    run(cmd, cwd=out_dir)
