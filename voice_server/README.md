# FlexToon voice server

Self-hosted text-to-speech with voice cloning. Runs on a GPU machine (such as an Unraid box)
and serves FlexToon over your local network, so nothing heavy runs on your laptop and there
are no per-use fees.

**Status: not yet run against the real model.** The server, auth, validation and FlexToon
client were tested with the built-in `dummy` backend. The Chatterbox backend follows that
project's documented Python API but has not been run. Chatterbox's README says the project
is retired (MIT licensed, no further updates); it works today, but plan for that.

## 1. Record your reference voice
Record 10-20 seconds of clean, natural speech in a quiet room (WAV). Only use a voice you
own or have permission to use. Save it as `voices/myvoice.wav`. The name before `.wav` is
the voice name FlexToon will ask for. Reference recordings are git-ignored; keep them private.

## 2. Run the server

### On Unraid (Docker, NVIDIA GPU)
1. Install the **Nvidia Driver** plugin from Community Applications and reboot if asked.
2. Copy this project to the server (for example `/mnt/user/appdata/flextoon`) and open a terminal there.
3. Create `voice_server/.env` with `VOICE_API_KEY=` followed by a long random string.
4. Put your recording in `voice_server/voices/`.
5. Run `docker compose -f voice_server/docker-compose.yml up -d --build`
   (needs the Compose Manager plugin, or run the equivalent `docker run` with `--runtime=nvidia`).
6. The first request downloads the model into `voice_server/models/`, so it is slow once.

No GPU? Remove the `deploy:` block. It will still work on CPU, just slowly.

### On any machine, no Docker
    pip install -r voice_server/requirements.txt
    export VOICE_API_KEY=some-long-random-string
    export VOICES_DIR=./voices
    python -m voice_server.server        # http://127.0.0.1:8000 (set HOST=0.0.0.0 for the network)

Set `VOICE_BACKEND=dummy` to test the plumbing without any model.

## 3. Point FlexToon at it
Copy `voice.local.example.json` to `voice.json`, set `url` to `http://<server-ip>:8000` and
`voice` to your voice name, and set the same key on the FlexToon machine:

    $env:FLEXTOON_VOICE_API_KEY = "the same long random string"     # PowerShell

## Security
The server requires the API key on every request, but it speaks plain HTTP. Keep it on your
LAN or behind a VPN; do not forward the port to the internet.

## Output watermark
Chatterbox adds an inaudible watermark (PerTh) to everything it generates.
