#!/usr/bin/env python3
"""Design a cartoon character from a description, from photos, or both.

  python characters.py create --name Sam \
      --description "friendly, navy polo, headset, short beard" \
      --photos a.jpg b.jpg c.jpg --i-have-rights-to-these-photos

More photos generally means a closer likeness, especially varied ones: different angles,
lighting and expressions, one person per photo, face clearly visible. Photos are
re-saved without EXIF/GPS metadata. The best few (up to max_reference_images) go to
the image model as direct references; the rest stay on file for a future training step.

The result is characters/<name>/character.json plus a reference sheet image. Use it in a
project with  "character": "<name>"  on a scene.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import providers

MAX_SIDE = 1536
STYLES = {
    "flat": "clean flat-colour cartoon, bold outlines",
    "3d": "stylised 3D animated-movie look, soft lighting",
    "comic": "comic-book style, inked outlines, halftone shading",
    "anime": "anime style, expressive eyes, cel shading",
}
SHEET_PROMPT = (
    "Character reference sheet on a plain light background: the same character in front view, "
    "three-quarter view and side view, plus three facial expressions (happy, surprised, serious). "
    "Consistent proportions, outfit and colours across every view. "
)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "character"


def _faces(path: Path) -> int | None:
    """Optional face count (needs OpenCV). None means unknown."""
    try:
        import cv2
        xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not xml.exists():  # some OpenCV builds ship without the data files
            return None
        cascade = cv2.CascadeClassifier(str(xml))
        if cascade.empty():
            return None
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        return len(cascade.detectMultiScale(img, 1.1, 5))
    except Exception:
        return None


def normalize_photo(src: Path, dst: Path) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        if min(w, h) < 512:
            warnings.append(f"{Path(src).name} is small ({w}x{h}); 1000px or larger works better.")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=92)  # re-saving drops EXIF/GPS metadata
    faces = _faces(dst)
    if faces == 0:
        warnings.append(f"{Path(src).name}: no face detected; it may not help.")
    elif faces and faces > 1:
        warnings.append(f"{Path(src).name}: {faces} faces found; use photos of only the person you want.")
    return {"file": dst.name, "pixels": im.size[0] * im.size[1], "faces": faces}, warnings


def advice(n_photos: int) -> str:
    if n_photos == 0:
        return "Description only: fine for an invented character, no likeness to a real person."
    if n_photos < 3:
        return "Add more photos (aim for 5 or more, varied angles and expressions) for a closer likeness."
    if n_photos < 8:
        return "Good. A few more varied photos (side angles, different lighting) would sharpen the likeness."
    return "Plenty of photos. Extra ones beyond the model's reference limit are kept for future training."


def _placeholder_sheet(name: str, description: str, style: str, photos: list[Path], out: Path) -> None:
    """Offline stand-in: a card with crude posterized versions of your photos."""
    w, h = 1536, 1024
    img = Image.new("RGB", (w, h), (245, 242, 235))
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
        small = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((60, 40), f"{name} (placeholder sheet)", fill=(30, 30, 40), font=big)
    d.text((60, 120), style, fill=(90, 90, 100), font=small)
    y, line = 170, ""
    for word in description.split():
        if len(line) + len(word) > 70:
            d.text((60, y), line, fill=(60, 60, 70), font=small)
            y, line = y + 40, ""
        line += word + " "
    d.text((60, y), line, fill=(60, 60, 70), font=small)
    x = 60
    for p in photos[:4]:
        with Image.open(p) as im:
            im = ImageOps.fit(im.convert("RGB"), (340, 440))
            im = im.filter(ImageFilter.MedianFilter(5))
            im = ImageOps.posterize(im, 3)
            im = ImageOps.autocontrast(im)
        img.paste(im, (x, 420))
        d.rectangle([x, 420, x + 340, 860], outline=(20, 20, 20), width=5)
        x += 370
    img.save(out)


def create(name: str, description: str, photos: list[Path], *, root: Path, style: str = "flat",
           provider_cfg: dict | None = None, rights_confirmed: bool = False,
           max_reference_images: int = 4) -> dict:
    if not (description.strip() or photos):
        raise ValueError("Give a description, one or more photos, or both.")
    if photos and not rights_confirmed:
        raise ValueError("Confirm you have the right to use these photos (yourself, or permission "
                         "from the people in them).")
    cfg = provider_cfg or {"provider": "placeholder"}
    cdir = root / slug(name)
    cdir.mkdir(parents=True, exist_ok=True)

    warnings, meta = [], []
    for i, p in enumerate(photos, 1):
        info, w = normalize_photo(Path(p), cdir / "photos" / f"photo{i:02}.jpg")
        meta.append(info)
        warnings += w

    # Best references first: exactly one face, then highest resolution.
    ranked = sorted(meta, key=lambda m: (m["faces"] == 1, m["pixels"]), reverse=True)
    refs = [cdir / "photos" / m["file"] for m in ranked[:max_reference_images]]

    style_text = STYLES.get(style, style)
    if refs:
        prompt = (SHEET_PROMPT + "Turn the person in the reference photos into a cartoon character. "
                  "Keep recognisable features: face shape, hair, skin tone, eyes, glasses, facial hair. "
                  f"Style: {style_text}. " + description)
    else:
        prompt = SHEET_PROMPT + f"Style: {style_text}. Character: {description}"

    sheet = cdir / "sheet.png"
    if cfg.get("provider", "placeholder") == "placeholder":
        _placeholder_sheet(name, description, style_text, refs, sheet)
    elif cfg["provider"] == "openai":
        sheet.write_bytes(providers.openai_image(prompt, cfg, refs or None))
    else:
        raise ValueError(f"Unknown image provider: {cfg['provider']}")

    record = {
        "name": name,
        "slug": slug(name),
        "style": style,
        # Reused in every scene prompt so the character stays on-model.
        "description": f"{name}: {description.strip()} ({style_text})".strip(),
        "photo_count": len(photos),
        "reference_count": len(refs),
        "sheet": str(sheet),
        "advice": advice(len(photos)),
        "warnings": warnings,
    }
    (cdir / "character.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def load(name: str, root: Path) -> dict:
    path = root / slug(name) / "character.json"
    if not path.exists():
        raise FileNotFoundError(f"No character named {name!r} in {root}. Run characters.py create first.")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["sheet"] = str(path.parent / "sheet.png")
    return record


def list_characters(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.glob("*/character.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--description", default="")
    c.add_argument("--photos", nargs="*", default=[])
    c.add_argument("--style", default="flat", help=f"one of {list(STYLES)} or your own words")
    c.add_argument("--provider", choices=["placeholder", "openai"], default="placeholder")
    c.add_argument("--root", default="characters")
    c.add_argument("--max-reference-images", type=int, default=4)
    c.add_argument("--i-have-rights-to-these-photos", action="store_true", dest="rights")
    ls = sub.add_parser("list")
    ls.add_argument("--root", default="characters")
    args = ap.parse_args()

    if args.cmd == "list":
        for rec in list_characters(Path(args.root)):
            print(f"{rec['slug']}: {rec['photo_count']} photos, style={rec['style']}")
        return
    try:
        rec = create(args.name, args.description, [Path(p) for p in args.photos], root=Path(args.root),
                     style=args.style, provider_cfg={"provider": args.provider},
                     rights_confirmed=args.rights, max_reference_images=args.max_reference_images)
    except ValueError as e:
        sys.exit(str(e))
    print(f"Created {rec['slug']}: {rec['sheet']}")
    print(rec["advice"])
    for w in rec["warnings"]:
        print("Warning:", w)


if __name__ == "__main__":
    main()
