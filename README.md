# Eyes Of Aziz — camera bridge

A reference bridge agent for [Project Aziz](https://projectaziz.com)'s **Eyes Of Aziz** camera
network: it pulls frames from an RTSP/CCTV camera on your local network and forwards them to the
Project Aziz backend, which checks them against active missing-person cases (via AWS Rekognition)
and alerts a human reviewer on a likely match. See the backend for the full design and
[github.com/mastashake08/jyronna-parker#49](https://github.com/mastashake08/jyronna-parker/issues/49).

Runs entirely outbound over HTTPS, so it works from behind NAT/firewalls without opening any
inbound ports — the same model Ring/Frigate use. The backend never touches the camera or the RTSP
protocol directly.

This is a starting point, not a hardened product: one camera per process, no live health/status
dashboard for a *running* bridge (the web dashboard covers setup/registration, not monitoring),
etc. It's meant to be simple enough to read end to end.

## Privacy

By default the bridge only uploads **faces**: it looks for faces in each frame on your own machine
and sends each one as a small crop. Frames with nobody in them never leave your network, and
neither does the rest of the scene around a face. The backend only keeps an upload if it matches
an active case; everything else is discarded right after the match check. The bridge doesn't
record anything locally — it only ever holds one frame in memory at a time.

Face detection uses OpenCV's [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
model (MIT licensed), bundled in `eyes_of_aziz/models/`. It runs in real time on an ordinary CPU,
including a Raspberry Pi 4.

## Requirements

- One or more ONVIF-capable cameras on the same local network as wherever this runs (most modern
  IP cameras — Hikvision, Dahua, Reolink, etc. — support ONVIF; a plain USB webcam doesn't)
- A Project Aziz account

## Download (Windows / macOS)

No Python install needed — grab the latest build for your OS from
[**Releases**](https://github.com/mastashake08/eyes-of-aziz/releases/latest), unzip it, and run
`eyes-of-aziz-setup` to open the setup dashboard in your browser.

These builds aren't code-signed yet, so your OS will warn you the first time you run one:

- **Windows**: SmartScreen says "Windows protected your PC" — click **More info**, then **Run
  anyway**.
- **macOS**: Gatekeeper blocks it outright on a plain double-click. Right-click (or Control-click)
  the app and choose **Open**, then confirm **Open** in the dialog — this only needs doing once.

## Install from source (any OS, incl. Linux)

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Setup: find and register your cameras

Two ways to run the same wizard — pick whichever you'd rather use:

```bash
eyes-of-aziz-setup          # terminal prompts
eyes-of-aziz-setup --web    # local web dashboard, opens in your browser
```

Either one:

1. Logs you into your Project Aziz account (`POST /api/login`, the same one the mobile app uses).
2. Scans the local network for ONVIF cameras (WS-Discovery). Note this only finds *candidates* —
   other WS-Discovery-speaking hardware (printers, NVRs, etc.) can show up too, which is why the
   next step exists.
3. For each one you choose to add, asks for *that camera's own* admin username/password — its
   local login, separate from your Project Aziz account — and confirms it's really an ONVIF
   camera by querying it directly and reading back its actual RTSP stream URL (rather than
   guessing at a brand-specific stream path).
4. Asks where the camera is (a street address, or latitude and longitude), so matches from it
   can be shown on a map. Addresses are looked up with OpenStreetMap's Nominatim.
5. Registers it with Project Aziz, writes `cameras/<name>.env`, and sends its first heartbeat
   with that location, so it shows as online right away.

`--web` binds to `127.0.0.1` only (default port `5151`, change with `--port`) — it's a
single-user local dashboard for setting up your own cameras, not something meant to be reachable
over the network. Your Project Aziz login token stays server-side in the running process; it's
never written to a browser cookie. Pass `--no-browser` to skip auto-opening a tab (e.g. over SSH),
and open the printed URL yourself, or forward the port.

If your camera doesn't support ONVIF, or isn't visible to WS-Discovery for some reason (routed
subnets, camera-side settings, etc.), you can still register it manually and fill in its RTSP URL
by hand:

```bash
curl -X POST https://projectaziz.com/api/field-devices/register \
  -H "Authorization: Bearer <your-sanctum-token>" \
  -H "Accept: application/json" \
  -F "device_id=front-door-camera-1" \
  -F "device_type=camera_bridge"
```

Then copy `.env.example` to `cameras/<name>.env` and fill in the response's `device_id`/
`registration_token` plus the camera's RTSP URL yourself.

Either way: keep the registration token secret — anyone with it can upload frames as that camera.

## Run

Run every camera you've registered, in one process:

```bash
eyes-of-aziz-bridge --all
```

That's also what happens when you run `eyes-of-aziz-bridge` (or double-click the downloaded
build) from the folder that has your `cameras/` directory and more than one camera in it. To run
just one camera, use `--env-file cameras/front-door-camera-1.env`. A camera that crashes is
restarted after 10 seconds; one that Project Aziz rejects (bad token, deactivated) is left
stopped until you fix it. Stop with Ctrl+C (or `SIGTERM`).

### Start automatically

```bash
eyes-of-aziz-bridge --install-service      # or the "Start automatically" button in setup
eyes-of-aziz-bridge --uninstall-service
```

Installs a per-user background service that runs `--all` and restarts it if it stops: a launchd
agent on macOS (log in `~/Library/Logs/eyes-of-aziz.log`), a systemd user unit on Linux, or a
logon task on Windows. No administrator rights needed. On Linux, run
`loginctl enable-linger $USER` too if it should run when nobody is logged in.

### Status page

While running, the bridge serves a status page at <http://127.0.0.1:5152/> (`--status-port` to
change it, `--no-status-page` to turn it off; JSON at `/status.json`). For each camera it shows
whether it's online or can't reach the camera, when it last sent a face and reported in, frame
counts, a missing location, and the last error. Like the setup dashboard, it's only reachable
from the same machine.

`.env.example` documents optional tuning (capture interval, face detection, heartbeat, JPEG
quality, retry/backoff settings) that applies to every camera.

## Recorders (NVRs)

If setup finds a network video recorder, it sees each connected camera as a separate channel and
offers to add all of them, named `<name>-ch1`, `<name>-ch2`, and so on, sharing one location. Each
channel is its own camera in Project Aziz and its own config under `cameras/`.

## Docker (Raspberry Pi, NAS)

An image for amd64 and arm64 is published as `ghcr.io/mastashake08/eyes-of-aziz` with each
release. From the folder you want your camera configs in:

```bash
# Set up cameras. --network host lets it find cameras on your network.
docker run -it --rm --network host -v "$PWD:/data" \
  --entrypoint eyes-of-aziz-setup ghcr.io/mastashake08/eyes-of-aziz

# Run every camera, restarting with the machine.
docker run -d --restart unless-stopped -v "$PWD:/data" -p 5152:5152 \
  ghcr.io/mastashake08/eyes-of-aziz
```

Or copy `docker-compose.example.yml` to `docker-compose.yml` and `docker compose up -d`. The status
page is on port 5152.

## Frigate

If you already run [Frigate](https://frigate.video), the bridge can use its person detection
instead of reading the stream itself: it listens for Frigate's person events over MQTT and checks
each event's snapshot for faces. Set these in the camera's config (no RTSP URL needed):

```bash
EYES_OF_AZIZ_SOURCE=frigate
EYES_OF_AZIZ_FRIGATE_URL=http://frigate.local:5000
EYES_OF_AZIZ_FRIGATE_CAMERA=driveway            # the camera's name in Frigate
EYES_OF_AZIZ_MQTT_URL=mqtt://user:password@mqtt.local:1883
# EYES_OF_AZIZ_FRIGATE_TOPIC_PREFIX=frigate     # if you changed Frigate's MQTT topic prefix
```

It takes the snapshot at the start of each sighting and the best one at its end.

## How it works

1. Opens the RTSP stream with OpenCV.
2. Every `EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS` (default 10s), grabs a frame. If the scene
   hasn't changed it skips it; otherwise it looks for faces. No faces, nothing is sent.
3. POSTs each face (as a JPEG crop, scaled up if small) to
   `{backend_url}/api/field-devices/{device_id}/frames` with the registration token —
   the same unauthenticated-but-token-checked pattern the backend already uses for its other field
   devices (`FieldDeviceController::ingestFrame`).
4. Every `EYES_OF_AZIZ_HEARTBEAT_INTERVAL_SECONDS` (default 5 minutes, and once on start) it
   sends a heartbeat to `{backend_url}/api/field-devices/{device_id}/status` with its version and
   location. That's what tells Project Aziz the camera is online. A failed heartbeat never stops
   frame uploads.
5. If the stream drops, it reconnects with exponential backoff. If a single upload fails, it logs
   and keeps going — a bad frame or a network blip shouldn't take the whole bridge down. If the
   backend rejects the device outright (401/403 — bad token, or the camera's been deactivated or
   pulled out of the network), it stops rather than spinning forever on a config problem.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Building the desktop apps

```bash
pip install -e ".[build]"
pyinstaller packaging/eyes_of_aziz.spec --distpath dist --noconfirm
```

Produces `dist/eyes-of-aziz-setup` and `dist/eyes-of-aziz-bridge`, standalone (no Python needed to
run them). PyInstaller doesn't cross-compile — this only builds for whatever OS you run it on.
`.github/workflows/build-desktop-apps.yml` builds both Windows and macOS on every push and, when
you push a `v*.*.*` tag, also creates a GitHub Release with each platform's build zipped up and
attached — that's the Releases page linked above, and what the website's download link should
point at.
