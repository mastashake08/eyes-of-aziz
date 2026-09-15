"""Find ONVIF-capable cameras on the local network and retrieve their RTSP
stream URL.

Two-step process, matching how ONVIF discovery actually works:

1. scan_network() broadcasts a WS-Discovery probe and returns every device
   that answers. WS-Discovery isn't camera-specific -- printers, NVRs, and
   other hardware use it too -- so a result here is a *candidate*, not a
   confirmed camera.
2. probe_camera() confirms a candidate is actually an ONVIF camera by
   calling its device/media services, using that camera's own local admin
   credentials (separate from your Project Aziz account login -- these are
   whatever the camera itself was configured with).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from onvif import ONVIFCamera
from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredDevice:
    xaddr: str
    host: str
    port: int


def scan_network(timeout: float = 3.0) -> list[DiscoveredDevice]:
    """Broadcasts a WS-Discovery probe on the local network and returns
    every device that answered within `timeout` seconds."""
    wsd = WSDiscovery()
    wsd.start()
    try:
        services = wsd.searchServices(timeout=timeout)
    finally:
        wsd.stop()

    devices: list[DiscoveredDevice] = []
    seen_xaddrs: set[str] = set()

    for service in services:
        for xaddr in service.getXAddrs():
            if xaddr in seen_xaddrs:
                continue
            seen_xaddrs.add(xaddr)

            parsed = urlparse(xaddr)
            if not parsed.hostname:
                logger.debug("Skipping unparseable discovery address: %s", xaddr)
                continue

            devices.append(
                DiscoveredDevice(
                    xaddr=xaddr,
                    host=parsed.hostname,
                    port=parsed.port or (443 if parsed.scheme == "https" else 80),
                )
            )

    return devices


@dataclass(frozen=True)
class CameraDetails:
    manufacturer: str | None
    model: str | None
    rtsp_url: str


class CameraProbeError(RuntimeError):
    """A discovered device didn't respond as an ONVIF camera -- either the
    credentials are wrong, or it isn't a camera at all."""


def probe_camera(device: DiscoveredDevice, username: str, password: str) -> CameraDetails:
    """Confirms `device` is an ONVIF camera and retrieves its default media
    profile's RTSP stream URL."""
    return asyncio.run(_probe_camera_async(device, username, password))


async def _probe_camera_async(
    device: DiscoveredDevice, username: str, password: str
) -> CameraDetails:
    camera = ONVIFCamera(device.host, device.port, username or None, password or None)

    try:
        await camera.update_xaddrs()

        manufacturer: str | None = None
        model: str | None = None
        try:
            devicemgmt = camera.create_devicemgmt_service()
            info = await devicemgmt.GetDeviceInformation()
            manufacturer = getattr(info, "Manufacturer", None)
            model = getattr(info, "Model", None)
        except Exception:
            logger.debug("Could not read device information for %s", device.host, exc_info=True)

        media = camera.create_media_service()
        profiles = await media.GetProfiles()
        if not profiles:
            raise CameraProbeError(f"{device.host} has no media profiles to stream from")

        stream_uri = await media.GetStreamUri(
            {
                "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
                "ProfileToken": profiles[0].token,
            }
        )

        return CameraDetails(manufacturer=manufacturer, model=model, rtsp_url=stream_uri.Uri)
    except CameraProbeError:
        raise
    except Exception as exc:
        raise CameraProbeError(
            f"{device.host} did not respond like an ONVIF camera "
            f"(wrong credentials, or it isn't a camera): {exc}"
        ) from exc
    finally:
        await camera.close()
