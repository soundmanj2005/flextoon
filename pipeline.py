#!/usr/bin/env python3
"""Script -> your voice -> scene images -> motion -> captions -> MP4.

Usage:
  python pipeline.py estimate project.json     # characters, length, cost estimate
  python pipeline.py build project.json        # full run (cached)
  python pipeline.py build project.json --scene s2 --force   # redo one scene
  python pipeline.py build project.json --stage voice        # voice only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import providers
import render


def load(path: str) -> dict:
    project = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [s["id"] for s in project["scenes"]]
    if len(ids) != len(set(ids)):
        sys.exit("Scene ids must be unique.")
    return project


def estimate(project: dict) -> None:
    text = " ".join(s["narration"] for s in project["scenes"])
    chars, words = len(text), len(text.split())
    print(f"Scenes: {len(project['scenes'])}")
    print(f"Narration: {words} words, {chars} characters")
    print(f"Estimated speech length: about {words / 150:.1f} minutes at 150 wpm")
    price = project.get("voice", {}).get("cost_per_million_chars")
    if price is not None:
        print(f"Estimated voice cost: ${chars * price / 1_000_000:.3f} (at ${price}/M chars)")
    else:
        print('Add "cost_per_million_chars" to the voice settings to see a cost estimate.')


def build(project: dict, args: argparse.Namespace) -> None:
    base = Path(args.workdir)
    w, h = project.get("resolution", [1280, 720])
    fps = project.get("fps", 30)
    pad = project.get("scene_padding_seconds", 0.5)
    scenes = [dict(sc) for sc in project["scenes"]]
    if args.scene and args.scene not in {sc["id"] for sc in scenes}:
        sys.exit(f"No scene with id {args.scene!r}.")

    def forced(sc: dict) -> bool:
        # --force applies to every scene, or only to --scene when given.
        return args.force and (not args.scene or sc["id"] == args.scene)

    # 1. Voice (cached by text + voice settings, so unchanged scenes cost nothing)
    for sc in scenes:
        sc["audio"] = providers.make_voice(sc, project["voice"], base / "audio", force=forced(sc))
        sc["speech_seconds"] = render.audio_duration(sc["audio"])
        print(f"[voice]  {sc['id']}: {sc['speech_seconds']:.1f}s")
    if args.stage == "voice":
        return

    # 2. Images (cached by prompt)
    for sc in scenes:
        sc["image"] = providers.make_image(
            sc, project.get("images", {}), project, base / "images", force=forced(sc)
        )
        print(f"[image]  {sc['id']}: {sc['image'].name}")
    if args.stage == "images":
        return

    # 3. Render clips + final
    clips = []
    for sc in scenes:
        sc["duration"] = sc["speech_seconds"] + pad
        clip = base / "clips" / f"{sc['id']}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        render.build_clip(sc["image"], sc["audio"], clip, sc["duration"],
                          sc.get("motion", "zoom_in"), w, h, fps)
        clips.append(clip)
        print(f"[clip]   {sc['id']}: {sc['duration']:.1f}s, motion={sc.get('motion', 'zoom_in')}")

    srt = None if project.get("captions", True) is False else render.build_srt(scenes)
    music = Path(project["music"]) if project.get("music") else None
    final = base / project.get("output", "final.mp4")
    style = project.get(
        "caption_style",
        "FontName=DejaVu Sans,FontSize=18,Bold=1,Outline=2,Shadow=0,MarginV=36,Alignment=2",
    )
    render.assemble(clips, base / "build", final, srt, music,
                    project.get("music_volume", 0.12), style)
    print(f"\nDone: {final}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["estimate", "build"])
    ap.add_argument("project")
    ap.add_argument("--workdir", default="out")
    ap.add_argument("--stage", choices=["all", "voice", "images"], default="all",
                    help="Stop after this stage (voice, images) or run everything")
    ap.add_argument("--scene", help="Only regenerate this scene id (others use cache)")
    ap.add_argument("--force", action="store_true", help="Regenerate instead of using cache")
    args = ap.parse_args()

    project = load(args.project)
    if args.command == "estimate":
        estimate(project)
    else:
        build(project, args)


if __name__ == "__main__":
    main()
