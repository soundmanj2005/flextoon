# FlexToon

Turn videos and scripts into cartoons, in your own voice.

License: MIT (see LICENSE).

- **Cartoonize uploaded videos** and choose the audio: keep the original, replace it with a generated voice, mix both, or mute.
- **Design cartoon characters** from a description, from one or more photos, or both. More (and more varied) photos give a closer likeness.
- **Script-to-video pipeline**: scenes with narration in a cloned voice, character images, motion, captions, music, MP4 export.
- **Local web app** for all of the above, plus a CLI for each piece.

## Status
Tested end to end offline: video cartoonizing, all four audio modes, character creation, the scene pipeline,
and the web app's routes and validation, all using placeholder voice/image providers.
**Not yet run against live services:** the ElevenLabs (voice, cloning) and OpenAI (images, image edits) calls follow
those services' documented REST APIs but have not been exercised. Face detection in photo checks is optional
and depends on your OpenCV build.

The default cartoon look is a local "toon shader" (smoothing, flat colour bands, ink outlines). It is a stylised
filter, not an AI redraw. Plug in a GPU model or API with `--external-command` for a true AI look.

## Setup
Python 3.10+ and FFmpeg (with libass) required.

    pip install -r requirements.txt

## Web app
    python app.py        # http://127.0.0.1:5000

Optional: create `voice.json` (`{"provider": "elevenlabs", "voice_id": "..."}`) and set `ELEVENLABS_API_KEY`;
set `FLEXTOON_IMAGE_PROVIDER=openai` and `OPENAI_API_KEY` for real character art. The app listens on
127.0.0.1 only; add authentication before exposing it to a network.

## CLI
    # Cartoonize a video, keep its audio
    python cartoonize.py in.mp4 out.mp4 --strength medium --audio keep
    # Replace the audio with a generated voice (freeze last frame if the voice is longer)
    python cartoonize.py in.mp4 out.mp4 --audio replace --text "What I want to say" --voice-config voice.json
    # Voice-over on top of the original, original lowered
    python cartoonize.py in.mp4 out.mp4 --audio mix --text "..." --original-volume 0.2

    # Design a character
    python characters.py create --name Sam --description "navy polo, headset, short beard" \
        --photos a.jpg b.jpg c.jpg d.jpg e.jpg --i-have-rights-to-these-photos

    # Script-to-video project
    cp project.example.json project.json
    python pipeline.py estimate project.json
    python pipeline.py build project.json
    python pipeline.py build project.json --scene s2 --force     # redo one scene

Use a character in a scene with `"character": "Sam"`. Voice and images are cached by content, so editing
one scene only pays for that scene.

## Self-hosted voice (no per-use fees)
FlexToon can run its own voice cloning on a GPU machine such as an Unraid box instead of a paid service.
See `voice_server/README.md` to run the server, then copy `voice.local.example.json` to `voice.json`
(`provider: "local_tts"`) and set `FLEXTOON_VOICE_API_KEY`. Tested with a dummy backend; the Chatterbox
backend (MIT licensed, but its project is retired) has not yet been run on real hardware.
Avoid models with non-commercial weights (for example XTTS-v2, F5-TTS) if you will sell the output.

## Your own voice (hosted option: ElevenLabs)
    export ELEVENLABS_API_KEY=...
    python clone_voice.py --name "My voice" --samples rec1.wav rec2.wav --i-confirm-this-is-my-voice

Only clone a voice you own or have written permission to clone. Only upload photos you have the right to use.
Check each provider's terms on whether uploads can be used for model training.

## Known limits
- No lip sync yet.
- Caption timing is estimated from word counts.
- One voice per video; per-character voices are planned.
- Character likeness from photos depends on the image model; more than a handful of photos are kept on file
  for a future training step rather than all sent as references.
