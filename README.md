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

The backend only keeps a frame if it matches an active case; everything else is discarded right
after the match check. This bridge doesn't add any local recording of its own — it only ever holds
one frame in memory at a time before uploading it.

## Requirements

- Python 3.10+
- One or more ONVIF-capable cameras on the same local network as wherever this runs (most modern
  IP cameras — Hikvision, Dahua, Reolink, etc. — support ONVIF; a plain USB webcam doesn't)
- A Project Aziz account

## Install

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
4. Registers it with Project Aziz and writes `cameras/<name>.env`, ready to run.

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

One bridge process per camera, each pointed at that camera's config file:

```bash
eyes-of-aziz-bridge --env-file cameras/front-door-camera-1.env
```

Running more than one camera means running this once per camera (e.g. as separate systemd units
or supervisor programs). Stop a bridge with Ctrl+C (or `SIGTERM`); it shuts down cleanly between
capture cycles.

`.env.example` documents optional tuning (capture interval, JPEG quality, retry/backoff settings)
that applies to every camera's config file.

## How it works

1. Opens the RTSP stream with OpenCV.
2. Every `EYES_OF_AZIZ_CAPTURE_INTERVAL_SECONDS` (default 10s), grabs a frame and JPEG-encodes it.
3. POSTs it to `{backend_url}/api/field-devices/{device_id}/frames` with the registration token —
   the same unauthenticated-but-token-checked pattern the backend already uses for its other field
   devices (`FieldDeviceController::ingestFrame`).
4. If the stream drops, it reconnects with exponential backoff. If a single upload fails, it logs
   and keeps going — a bad frame or a network blip shouldn't take the whole bridge down. If the
   backend rejects the device outright (401/403 — bad token, or the camera's been deactivated or
   pulled out of the network), it stops rather than spinning forever on a config problem.

## Development

```bash
pip install -e ".[dev]"
pytest
```
