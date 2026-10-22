"""Starting configs for the bridge's Reticulum node (rnsd) and propagation
node (lxmd). Written once by the installer; edit them by hand after that.
"""

from __future__ import annotations

from pathlib import Path

SERIAL_BY_ID = Path("/dev/serial/by-id")
# USB-serial chips on the boards that run RNode / Aziz node-mode firmware:
# Heltec V3 (CP2102), V4 and other ESP32-S3 boards (native USB), CH340/CH9102.
_RNODE_CHIPS = ("CP210", "CH34", "CH91", "USB_JTAG", "Espressif", "RNode")

DEFAULT_BACKBONES = (("projectaziz.com", 4242), ("rns.beleth.net", 4242))
LISTEN_PORT = 4242
# Same radio settings as the app, the Aziz Node and the repeater bridges.
RADIO = {"frequency": 915000000, "bandwidth": 125000, "txpower": 17, "spreadingfactor": 8, "codingrate": 5}


def find_rnode_ports(by_id: Path = SERIAL_BY_ID) -> list[str]:
    """Stable /dev/serial/by-id paths of plugged-in LoRa boards."""
    try:
        entries = sorted(by_id.iterdir())
    except OSError:
        return []
    return [str(entry) for entry in entries if any(chip in entry.name for chip in _RNODE_CHIPS)]


def render_reticulum_config(
    rnode_port: str | None = None,
    backbones: tuple[tuple[str, int], ...] = DEFAULT_BACKBONES,
    listen_port: int = LISTEN_PORT,
) -> str:
    lines = [
        "# Eyes Of Aziz Bridge - Reticulum transport node.",
        "",
        "[reticulum]",
        "  enable_transport = Yes",
        "  share_instance = Yes",
        "  instance_name = default",
        "",
        "[logging]",
        "  loglevel = 4",
        "",
        "[interfaces]",
        "  [[Local Network]]",
        "    type = AutoInterface",
        "    enabled = Yes",
        "",
        "  # Phones and other nodes on this network connect here.",
        "  [[Bridge Listener]]",
        "    type = BackboneInterface",
        "    enabled = Yes",
        "    listen_on = 0.0.0.0",
        f"    port = {listen_port}",
    ]
    for host, port in backbones:
        lines += [
            "",
            f"  [[Backbone {host}]]",
            "    type = BackboneInterface",
            "    enabled = Yes",
            f"    remote = {host}",
            f"    target_port = {port}",
        ]
    if rnode_port:
        lines += [
            "",
            "  # A Heltec in node mode, or any RNode, on USB.",
            "  [[RNode LoRa]]",
            "    type = RNodeInterface",
            "    interface_enabled = True",
            f"    port = {rnode_port}",
            *(f"    {key} = {value}" for key, value in RADIO.items()),
        ]
    return "\n".join(lines) + "\n"


def render_lxmd_config(node_name: str, announce_interval_minutes: int = 30) -> str:
    """A propagation node: stores messages for offline recipients and peers
    with other propagation nodes, so messages reach phones that sync here."""
    return "\n".join([
        "# Eyes Of Aziz Bridge - LXMF propagation node.",
        "",
        "[propagation]",
        "  enable_node = yes",
        f"  node_name = {node_name}",
        "  announce_at_start = yes",
        f"  announce_interval = {announce_interval_minutes}",
        "  autopeer = yes",
        "  autopeer_maxdepth = 6",
        "  # MB of messages kept for offline recipients.",
        "  message_storage_limit = 500",
        "",
        "[lxmf]",
        f"  display_name = {node_name}",
        "  announce_at_start = no",
        "",
        "[logging]",
        "  loglevel = 4",
    ]) + "\n"
