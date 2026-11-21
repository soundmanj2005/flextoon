#!/usr/bin/env python3
"""Local web app: cartoonize uploaded videos and design characters from text or photos.

  pip install flask
  python app.py            # http://127.0.0.1:5000

Config (environment):
  FLEXTOON_DATA            where uploads/results live (default ./data)
  FLEXTOON_MAX_MB          max upload size (default 500)
  FLEXTOON_IMAGE_PROVIDER  placeholder (default) or openai   (openai needs OPENAI_API_KEY)
  voice.json in this folder sets the voice for generated audio, for example
      {"provider": "elevenlabs", "voice_id": "YOUR_CLONED_VOICE_ID"}   (needs ELEVENLABS_API_KEY)
Binds to 127.0.0.1 only. Don't expose it to the internet without adding authentication.
"""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_file

import cartoonize
import characters

BASE = Path(os.environ.get("FLEXTOON_DATA", "data")).resolve()
JOBS_DIR, CHAR_ROOT = BASE / "jobs", BASE / "characters"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
CHAR_ROOT.mkdir(parents=True, exist_ok=True)

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_NARRATION_CHARS = 5000
MAX_PHOTOS = 20
ID_RE = re.compile(r"^[0-9a-f]{32}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("FLEXTOON_MAX_MB", "500")) * 1024 * 1024

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def voice_cfg() -> dict:
    p = Path("voice.json")
    return json.loads(p.read_text()) if p.exists() else {"provider": "placeholder"}


def image_cfg() -> dict:
    return {"provider": os.environ.get("FLEXTOON_IMAGE_PROVIDER", "placeholder")}


def start_job(fn, *args, job_id: str | None = None, **kwargs) -> str:
    job_id = job_id or uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {"status": "running", "message": "Working...", "result": None}

    def run():
        try:
            result = fn(job_id, *args, **kwargs)
            with LOCK:
                JOBS[job_id] = {"status": "done", "message": result.get("message", "Done"), "result": result}
        except Exception as e:  # shown to the user; details stay in the server log
            traceback.print_exc()
            with LOCK:
                JOBS[job_id] = {"status": "error", "message": str(e) or e.__class__.__name__, "result": None}

    threading.Thread(target=run, daemon=True).start()
    return job_id


def save_uploads(files, allowed: set[str], dest: Path, prefix: str) -> list[Path]:
    saved = []
    for i, f in enumerate(files):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in allowed:
            raise ValueError(f"Unsupported file type: {ext or 'unknown'}")
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{prefix}{i}{ext}"  # never use the client's filename on disk
        f.save(path)
        saved.append(path)
    return saved


# ---------------------------------------------------------------- jobs

def video_job(job_id: str, src: Path, opts: dict) -> dict:
    out = JOBS_DIR / job_id / "result.mp4"
    cartoonize.process(
        src, out, strength=opts["strength"], audio=opts["audio"], narration=opts["narration"],
        voice_cfg=voice_cfg(), original_volume=opts["original_volume"], fit=opts["fit"],
        workdir=JOBS_DIR / job_id / "work",
    )
    return {"message": "Your cartoon is ready.", "url": f"/api/download/{job_id}"}


def character_job(job_id: str, name: str, description: str, photos: list[Path], style: str) -> dict:
    rec = characters.create(name, description, photos, root=CHAR_ROOT, style=style,
                            provider_cfg=image_cfg(), rights_confirmed=True)
    return {"message": rec["advice"], "slug": rec["slug"], "warnings": rec["warnings"],
            "url": f"/api/characters/{rec['slug']}/sheet"}


# ---------------------------------------------------------------- routes

@app.post("/api/cartoonize")
def api_cartoonize():
    f = request.files.get("video")
    if not f:
        return jsonify(error="Choose a video file."), 400
    audio = request.form.get("audio", "keep")
    strength = request.form.get("strength", "medium")
    fit = request.form.get("fit", "freeze")
    narration = (request.form.get("narration") or "").strip()
    try:
        original_volume = min(1.0, max(0.0, float(request.form.get("original_volume", 0.25))))
    except ValueError:
        return jsonify(error="Invalid original volume."), 400
    if audio not in cartoonize.AUDIO_MODES or strength not in cartoonize.PRESETS or fit not in ("freeze", "trim"):
        return jsonify(error="Invalid option."), 400
    if audio in ("replace", "mix") and not narration:
        return jsonify(error="Enter the narration text to use for the new audio."), 400
    if len(narration) > MAX_NARRATION_CHARS:
        return jsonify(error=f"Narration is limited to {MAX_NARRATION_CHARS} characters."), 400
    job_id = uuid.uuid4().hex  # also the folder name for this job's files
    try:
        (src,) = save_uploads([f], VIDEO_EXT, JOBS_DIR / job_id, "input")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    opts = dict(strength=strength, audio=audio, narration=narration,
                original_volume=original_volume, fit=fit)
    return jsonify(job=start_job(video_job, src, opts, job_id=job_id))


@app.post("/api/characters")
def api_characters_create():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    style = (request.form.get("style") or "flat").strip()[:80]
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not name or len(name) > 60:
        return jsonify(error="Give the character a name (60 characters or fewer)."), 400
    if not (description or files):
        return jsonify(error="Add a description, one or more photos, or both."), 400
    if len(files) > MAX_PHOTOS:
        return jsonify(error=f"Up to {MAX_PHOTOS} photos."), 400
    if files and request.form.get("rights") != "yes":
        return jsonify(error="Confirm you have the right to use these photos."), 400
    photo_dir = JOBS_DIR / uuid.uuid4().hex / "photos"
    try:
        photos = save_uploads(files, IMAGE_EXT, photo_dir, "photo")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(job=start_job(character_job, name, description, photos, style))


@app.get("/api/characters")
def api_characters_list():
    return jsonify([{k: r[k] for k in ("name", "slug", "style", "photo_count", "description")}
                    for r in characters.list_characters(CHAR_ROOT)])


@app.get("/api/characters/<slug>/sheet")
def api_character_sheet(slug: str):
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        abort(404)
    p = CHAR_ROOT / slug / "sheet.png"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/png")


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    if not ID_RE.match(job_id):
        abort(404)
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404)
    return jsonify(job)


@app.get("/api/download/<job_id>")
def api_download(job_id: str):
    if not ID_RE.match(job_id):
        abort(404)
    p = JOBS_DIR / job_id / "result.mp4"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="video/mp4", as_attachment=request.args.get("dl") == "1",
                     download_name="cartoon.mp4")


@app.get("/")
def index():
    return render_template_string(PAGE, voice=voice_cfg().get("provider", "placeholder"),
                                  image=image_cfg()["provider"])


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FlexToon</title>
<style>
 body{font:16px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1c1c24}
 h1{margin-bottom:0} .sub{color:#666;margin-top:.2rem}
 section{border:1px solid #ddd;border-radius:12px;padding:1.2rem 1.4rem;margin:1.4rem 0}
 label{display:block;margin:.8rem 0 .2rem;font-weight:600} input[type=text],textarea,select{width:100%;padding:.5rem;box-sizing:border-box}
 textarea{min-height:90px} button{margin-top:1rem;padding:.6rem 1.2rem;font-size:1rem;cursor:pointer}
 .row{display:flex;gap:1rem;flex-wrap:wrap}.row>div{flex:1;min-width:200px} .hint{color:#666;font-size:.9rem}
 .status{margin-top:1rem;padding:.6rem;border-radius:8px;background:#f3f3f6;display:none}.err{background:#fde8e8}
 video,img{max-width:100%;border-radius:8px;margin-top:.8rem}
</style>
<h1>FlexToon</h1>
<p class="sub">Voice: <b>{{voice}}</b> &middot; Images: <b>{{image}}</b>
 (placeholder means offline test mode; see README to switch to real services)</p>

<section><h2>1. Cartoonize a video</h2>
<form id="vform">
 <label>Video file</label><input type="file" name="video" accept="video/*" required>
 <div class="row">
  <div><label>Cartoon strength</label><select name="strength">
   <option>light</option><option selected>medium</option><option>strong</option></select></div>
  <div><label>Audio</label><select name="audio" id="audio">
   <option value="keep">Keep the video's original audio</option>
   <option value="replace">Replace with generated voice</option>
   <option value="mix">Generated voice over original (original lowered)</option>
   <option value="mute">No audio</option></select></div>
 </div>
 <div id="narr" style="display:none">
  <label>What should the new voice say?</label><textarea name="narration" maxlength="5000"></textarea>
  <div class="row">
   <div><label>If the voice runs longer than the video</label><select name="fit">
    <option value="freeze">Freeze the last frame until it finishes</option>
    <option value="trim">Cut the voice at the end of the video</option></select></div>
   <div id="ov" style="display:none"><label>Original audio level (mix)</label>
    <input type="range" name="original_volume" min="0" max="1" step="0.05" value="0.25"></div>
  </div>
 </div>
 <button>Cartoonize</button>
</form><div class="status" id="vstatus"></div><div id="vresult"></div></section>

<section><h2>2. Design a character</h2>
<form id="cform">
 <div class="row"><div><label>Name</label><input type="text" name="name" required maxlength="60"></div>
 <div><label>Style</label><select name="style"><option value="flat">Flat cartoon</option>
  <option value="3d">3D animated</option><option value="comic">Comic book</option><option value="anime">Anime</option></select></div></div>
 <label>Description</label>
 <textarea name="description" placeholder="e.g. friendly, navy polo shirt, headset, short beard"></textarea>
 <label>Photos (optional, more is better)</label>
 <input type="file" name="photos" accept="image/*" multiple>
 <p class="hint">Use clear photos of one person each, with varied angles, lighting and expressions. 5 or more
 gives a closer likeness. Photos are re-saved without location/camera metadata.</p>
 <label><input type="checkbox" name="rights" value="yes"> I have the right to use these photos
  (they are me, or I have permission from the people in them)</label>
 <button>Create character</button>
</form><div class="status" id="cstatus"></div><div id="cresult"></div></section>

<script>
const $=s=>document.querySelector(s);
function show(el,msg,err){el.style.display='block';el.className='status'+(err?' err':'');el.textContent=msg}
$('#audio').onchange=e=>{const v=e.target.value;$('#narr').style.display=(v==='replace'||v==='mix')?'block':'none';$('#ov').style.display=v==='mix'?'block':'none'};
async function submit(form,url,status,result,render){
 result.replaceChildren();show(status,'Uploading...');
 let r=await fetch(url,{method:'POST',body:new FormData(form)});let j=await r.json();
 if(!r.ok){show(status,j.error||'Something went wrong',true);return}
 show(status,'Working... this can take a while for longer videos.');
 for(;;){await new Promise(t=>setTimeout(t,1500));
  const s=await (await fetch('/api/jobs/'+j.job)).json();
  if(s.status==='running')continue;
  if(s.status==='error'){show(status,s.message,true);return}
  show(status,s.message+((s.result.warnings||[]).length?'  Warnings: '+s.result.warnings.join(' '):''));
  render(result,s.result);return}}
$('#vform').onsubmit=e=>{e.preventDefault();submit(e.target,'/api/cartoonize',$('#vstatus'),$('#vresult'),(box,res)=>{
 const v=document.createElement('video');v.controls=true;v.src=res.url;box.append(v);
 const a=document.createElement('a');a.href=res.url+'?dl=1';a.textContent='Download MP4';a.style.display='block';box.append(a)})};
$('#cform').onsubmit=e=>{e.preventDefault();submit(e.target,'/api/characters',$('#cstatus'),$('#cresult'),(box,res)=>{
 const i=document.createElement('img');i.src=res.url+'?t='+Date.now();box.append(i)})};
</script>"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
