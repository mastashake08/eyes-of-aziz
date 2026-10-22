import os

import pytest

from eyes_of_aziz.mesh import (
    commands,
    hardware,
    heartbeat,
    interfaces,
    registration,
    rns_config,
)
from eyes_of_aziz.mesh.cli import main
from eyes_of_aziz.mesh.store import MeshHome, normalize_address

PHONE = "4b9aa84ac056c60e9b937e7a6551c213"
OTHER = "9ea57d0185eb2aa97baf3d8fe547df27"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EYES_OF_AZIZ_MESH_HOME", str(tmp_path / "mesh"))
    home = MeshHome.default()
    home.ensure()
    return home


# --- hardware -------------------------------------------------------------------

@pytest.mark.parametrize(
    "model, memory, expected",
    [
        ("Raspberry Pi 3 Model B Rev 1.2", 1_000_000_000, False),
        ("Raspberry Pi 3 Model B Plus Rev 1.3", 1_000_000_000, False),
        ("Raspberry Pi 4 Model B Rev 1.4", 2_000_000_000, True),
        ("Raspberry Pi 4 Model B Rev 1.4", 1_000_000_000, True),  # the model decides on a Pi
        ("Raspberry Pi 5 Model B Rev 1.0", 8_000_000_000, True),
        ("Raspberry Pi Compute Module 4 Rev 1.1", 4_000_000_000, True),
        ("Raspberry Pi Zero 2 W Rev 1.0", 500_000_000, False),
        (None, 8_000_000_000, True),   # a mini PC
        (None, 1_000_000_000, False),
        (None, None, False),
    ],
)
def test_cameras_need_a_pi_4_or_5(model, memory, expected):
    assert hardware.cameras_supported(model, memory) is expected


def test_reads_model_and_memory(tmp_path):
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi 3 Model B Rev 1.2\x00")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:         926840 kB\nMemFree: 1 kB\n")
    assert hardware.read_model(model) == "Raspberry Pi 3 Model B Rev 1.2"
    assert hardware.total_memory(meminfo) == 926840 * 1024
    assert hardware.read_model(tmp_path / "missing") is None


# --- store ------------------------------------------------------------------------

def test_normalize_address():
    assert normalize_address(f"<{PHONE.upper()}>") == PHONE
    with pytest.raises(ValueError):
        normalize_address("not-an-address")


def test_allowed_list(home):
    assert home.allowed() == set()
    assert home.allow(PHONE) is True
    assert home.allow(PHONE) is False
    home.path("allowed").write_text(home.path("allowed").read_text() + "# a comment\nbogus\n")
    assert home.allowed() == {PHONE}


def test_outbox_round_trip_oldest_first(home):
    home.queue({"to": PHONE, "text": "one"})
    home.queue({"to": OTHER, "text": "two"})
    (home.outbox / "broken.json").write_text("{not json")
    taken = home.take_outbox()
    assert [item["text"] for item in taken] == ["one", "two"]
    assert home.take_outbox() == []


def test_device_file_is_private(home):
    home.write_json("device.json", {"registration_token": "secret"}, private=True)
    assert oct(os.stat(home.path("device.json")).st_mode & 0o777) == "0o600"
    assert home.read_json("device.json") == {"registration_token": "secret"}


# --- commands ---------------------------------------------------------------------

def test_ping_and_help_for_anyone():
    assert commands.decide("/ping", OTHER, False, set()).reply == "pong"
    assert "/sh" in commands.decide("/help", OTHER, False, set()).reply


def test_shell_only_for_signed_allowed_senders():
    assert commands.decide("/sh uptime", PHONE, True, {PHONE}).shell == "uptime"
    assert commands.decide("/sh uptime", PHONE, False, {PHONE}).reply == commands.NOT_ALLOWED
    assert commands.decide("/sh uptime", OTHER, True, {PHONE}).reply == commands.NOT_ALLOWED
    assert commands.decide("/sh", PHONE, True, {PHONE}).reply.startswith("Usage")


def test_ordinary_messages_get_no_reply():
    decision = commands.decide("hello there", OTHER, True, set())
    assert decision.reply is None and decision.shell is None


def test_shell_reply_is_capped():
    reply = commands.format_shell_reply(0, "x" * 5000, 120)
    assert reply.startswith("exit 0") and reply.endswith("(truncated)")
    assert len(reply) < commands.MAX_REPLY + 20
    assert commands.format_shell_reply(None, "", 120) == "timed out after 120s"


# --- interfaces -------------------------------------------------------------------

def test_interface_summary_skips_plumbing():
    stats = {
        "transport_id": b"\x01" * 16,
        "interfaces": [
            {"name": "Shared Instance[37428]", "status": True},
            {"name": "BackboneInterface[Bridge Listener/0.0.0.0:4242]", "status": True},
            {"name": "BackboneInterface[Client on Bridge Listener]", "status": True},
            {"name": "BackboneInterface[Backbone projectaziz.com/projectaziz.com:4242]", "status": False},
            {"name": "RNodeInterface[RNode LoRa]", "status": True},
        ],
    }
    assert interfaces.summarize(stats) == [
        {"name": "Bridge Listener", "up": True},
        {"name": "Backbone projectaziz.com", "up": False},
        {"name": "RNode LoRa", "up": True},
    ]
    assert interfaces.transport_enabled(stats) is True
    assert interfaces.transport_enabled({"interfaces": []}) is False
    assert interfaces.summarize(None) == []


# --- configs ----------------------------------------------------------------------

def test_reticulum_config_is_a_transport_node_with_lora(tmp_path):
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0").touch()
    (by_id / "usb-Some_Mouse-if00").touch()
    ports = rns_config.find_rnode_ports(by_id)
    assert len(ports) == 1 and "CP2102" in ports[0]

    text = rns_config.render_reticulum_config(rnode_port=ports[0])
    assert "enable_transport = Yes" in text
    assert "listen_on = 0.0.0.0" in text and "port = 4242" in text
    assert "remote = projectaziz.com" in text
    assert f"port = {ports[0]}" in text and "spreadingfactor = 8" in text
    assert "RNodeInterface" not in rns_config.render_reticulum_config(rnode_port=None)


def test_lxmd_config_runs_a_propagation_node():
    text = rns_config.render_lxmd_config("Eyes Of Aziz Bridge pi")
    assert "enable_node = yes" in text
    assert "node_name = Eyes Of Aziz Bridge pi" in text


def test_write_configs_keeps_existing_ones(tmp_path, capsys):
    reticulum = tmp_path / "rns"
    reticulum.mkdir()
    (reticulum / "config").write_text("mine")
    main(["write-configs", "--rnode", "none", "--reticulum-dir", str(reticulum), "--lxmd-dir", str(tmp_path / "lxmd")])
    assert (reticulum / "config").read_text() == "mine"
    assert "enable_node = yes" in (tmp_path / "lxmd" / "config").read_text()


# --- heartbeat --------------------------------------------------------------------

DEVICE = {
    "backend_url": "https://projectaziz.test/",
    "device_id": "eoa-bridge-abc123def456",
    "registration_token": "tok",
    "latitude": 38.25,
    "longitude": -85.75,
}
SNAPSHOT = {
    "lxmf_address": PHONE,
    "propagation_node": OTHER,
    "shell_address": None,
    "transport": True,
    "cameras": False,
    "interfaces": [{"name": "RNode LoRa", "up": True}],
    "inbox": 3,  # not sent
}


def test_heartbeat_payload_carries_mesh_details_and_location():
    payload = heartbeat.build_payload(DEVICE, SNAPSHOT)
    assert payload["registration_token"] == "tok"
    assert payload["mesh"] == {
        "lxmf_address": PHONE,
        "propagation_node": OTHER,
        "transport": True,
        "cameras": False,
        "interfaces": [{"name": "RNode LoRa", "up": True}],
    }
    assert (payload["latitude"], payload["longitude"]) == (38.25, -85.75)


def test_heartbeat_posts_to_the_device_status_endpoint(requests_mock):
    url = "https://projectaziz.test/api/field-devices/eoa-bridge-abc123def456/status"
    requests_mock.post(url, json={"status": "updated"})
    heartbeat.send(DEVICE, SNAPSHOT)
    assert requests_mock.last_request.json()["mesh"]["lxmf_address"] == PHONE

    requests_mock.post(url, status_code=401, json={"message": "Invalid device"})
    with pytest.raises(heartbeat.HeartbeatError):
        heartbeat.send(DEVICE, SNAPSHOT)


# --- registration -----------------------------------------------------------------

def test_device_id_is_stable_per_machine(tmp_path):
    machine_id = tmp_path / "machine-id"
    machine_id.write_text("b827eb123456aaaabbbbccccdddd0000\n")
    assert registration.device_id(machine_id) == "eoa-bridge-b827eb123456"
    assert registration.device_id(tmp_path / "missing").startswith("eoa-bridge-")


def test_registers_as_an_eoa_bridge(requests_mock, tmp_path):
    from eyes_of_aziz.account_client import AccountClient

    machine_id = tmp_path / "machine-id"
    machine_id.write_text("b827eb123456aaaabbbbccccdddd0000")
    requests_mock.post(
        "https://projectaziz.test/api/field-devices/register",
        json={"device": {"device_id": "eoa-bridge-b827eb123456", "registration_token": "fresh"}},
    )
    saved = registration.register(
        AccountClient("https://projectaziz.test"), "bearer", "Garage Pi", "https://projectaziz.test",
        (38.25, -85.75), machine_id,
    )
    sent = requests_mock.last_request.json()
    assert sent["device_type"] == "eoa_bridge" and sent["label"] == "Garage Pi"
    assert requests_mock.last_request.headers["Authorization"] == "Bearer bearer"
    assert saved == {
        "backend_url": "https://projectaziz.test",
        "device_id": "eoa-bridge-b827eb123456",
        "registration_token": "fresh",
        "label": "Garage Pi",
        "latitude": 38.25,
        "longitude": -85.75,
    }


# --- cli --------------------------------------------------------------------------

def test_cli_send_allow_and_logs(home, capsys):
    assert main(["send", PHONE.upper(), "hello", "there"]) == 0
    assert home.take_outbox() == [{"to": PHONE, "text": "hello there"}]
    assert main(["send", "nope", "hi"]) == 2

    assert main(["allow", PHONE]) == 0
    assert home.allowed() == {PHONE}

    home.log("inbox.jsonl", {"from": OTHER, "text": "hi bridge"})
    capsys.readouterr()
    main(["inbox"])
    assert "hi bridge" in capsys.readouterr().out


def test_cli_status_reads_the_agents_snapshot(home, capsys):
    assert main(["status"]) == 1
    home.write_json("status.json", {"lxmf_address": PHONE})
    assert main(["status"]) == 0
    assert PHONE in capsys.readouterr().out
