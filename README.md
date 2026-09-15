# Eyes Of Aziz — camera bridge

A reference bridge agent for [Project Aziz](https://projectaziz.com)'s **Eyes Of Aziz** camera
network: it pulls frames from an RTSP/CCTV camera on your local network and forwards them to the
Project Aziz backend, which checks them against active missing-person cases (via AWS Rekognition)
and alerts a human reviewer on a likely match. See the backend for the full design and
[github.com/mastashake08/jyronna-parker#49](https://github.com/mastashake08/jyronna-parker/issues/49).

Runs entirely outbound over HTTPS, so it works from behind NAT/firewalls without opening any
inbound ports — the same model Ring/Frigate use. The backend never touches the camera or the RTSP
protocol directly.

This is a starting point, not a hardened product: one camera per process, no health dashboard,
etc. It's meant to be simple enough to read end to end.

## Privacy

The backend only keeps a frame if it matches an active case; everything else is discarded right
after the match check. This bridge doesn't add any local recording of its own — it only ever holds
one frame in memory at a time before uploading it.

## Requirements

- Python 3.10+
- An RTSP-capable camera reachable from wherever this runs
- A `device_id` and `registration_token` for that camera (see below)

## Getting a device_id and registration_token

The backend's UI for registering a camera isn't built yet, so for now, register directly against
the API with a Sanctum-authenticated account:

```bash
curl -X POST https://projectaziz.com/api/field-devices/register \
  -H "Authorization: Bearer <your-sanctum-token>" \
  -H "Accept: application/json" \
  -F "device_id=front-door-camera-1" \
  -F "device_type=camera_bridge"
```

The response includes the `device_id` and `registration_token` you'll put in `.env`. Keep the
token secret — anyone with it can upload frames as this camera.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configure

```bash
cp .env.example .env
```

Fill in `EYES_OF_AZIZ_BACKEND_URL`, `EYES_OF_AZIZ_DEVICE_ID`, `EYES_OF_AZIZ_REGISTRATION_TOKEN`,
and `EYES_OF_AZIZ_RTSP_URL`. Everything else in `.env.example` is optional tuning with sane
defaults — see the comments there.

## Run

```bash
python -m eyes_of_aziz
# or, after `pip install -e .`:
eyes-of-aziz-bridge
```

Stop it with Ctrl+C (or `SIGTERM`); it shuts down cleanly between capture cycles.

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
