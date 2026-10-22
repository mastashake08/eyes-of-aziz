"""eyes-of-aziz-mesh: run and use the Eyes Of Aziz Bridge's mesh side.

  run                         the agent (the eyes-of-aziz-mesh service runs this)
  setup                       sign in and register this bridge with Project Aziz
  send <address> <text...>    queue a message
  inbox [-n N] / sent [-n N]  messages received / send results
  allow <address>             let that LXMF address run admin commands
  announce                    announce this bridge's address now
  address                     this bridge's LXMF address
  status                      what the agent last reported
  cameras-supported           exit 0 if this machine can run the camera bridge
  write-configs               first-time rnsd/lxmd configs (used by the installer)
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import socket
import sys
from pathlib import Path

from ..account_client import AccountClient, AccountError
from ..location import LocationError, parse_location
from . import hardware, registration, rns_config
from .store import MeshHome, normalize_address

DEFAULT_BACKEND_URL = os.environ.get("EYES_OF_AZIZ_BACKEND_URL", "https://projectaziz.com")


def _print_records(records: list[dict]) -> None:
    if not records:
        print("(none)")
    for record in records:
        who = record.get("from") or record.get("to")
        state = f" [{record['state']}]" if "state" in record else ""
        print(f"{record.get('at', '')}  {who}{state}  {record.get('text', '')}")


def _setup(home: MeshHome, args: argparse.Namespace) -> int:
    if home.read_json(registration.DEVICE_FILE) and not args.again:
        print("This bridge is already registered (use --again to register it again).")
        return 0
    backend_url = args.backend_url
    email = args.email or input("Project Aziz email: ").strip()
    password = getpass.getpass("Project Aziz password: ")
    label = args.label or input(f"Name for this bridge [{socket.gethostname()}]: ").strip() or socket.gethostname()
    location = None
    while True:
        text = args.location if args.location is not None else input(
            "Where is it? Address or 'latitude, longitude' (blank to skip): "
        )
        try:
            location = parse_location(text)
            break
        except LocationError as exc:
            print(exc)
            if args.location is not None:
                return 1

    account = AccountClient(backend_url)
    try:
        token = account.login(email, password)
        device = registration.register(account, token, label, backend_url, location)
    except AccountError as exc:
        print(exc)
        return 1
    home.ensure()
    home.write_json(registration.DEVICE_FILE, device, private=True)
    print(f"Registered {label} ({device['device_id']}). It shows in the Project Aziz app under your hardware.")
    return 0


def _write_configs(args: argparse.Namespace) -> int:
    reticulum = Path(args.reticulum_dir).expanduser() / "config"
    lxmd = Path(args.lxmd_dir).expanduser() / "config"
    rnode = None
    if args.rnode == "auto":
        ports = rns_config.find_rnode_ports()
        rnode = ports[0] if ports else None
    elif args.rnode != "none":
        rnode = args.rnode

    for path, text in (
        (reticulum, rns_config.render_reticulum_config(rnode_port=rnode)),
        (lxmd, rns_config.render_lxmd_config(args.name)),
    ):
        if path.exists() and not args.force:
            print(f"Kept existing {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"Wrote {path}")
    if rnode:
        print(f"LoRa radio: {rnode}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eyes-of-aziz-mesh", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the agent")
    setup = sub.add_parser("setup", help="register this bridge with Project Aziz")
    setup.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    setup.add_argument("--email")
    setup.add_argument("--label")
    setup.add_argument("--location")
    setup.add_argument("--again", action="store_true")
    send = sub.add_parser("send", help="queue a message")
    send.add_argument("address")
    send.add_argument("text", nargs="+")
    for name in ("inbox", "sent"):
        sub.add_parser(name).add_argument("-n", type=int, default=20)
    sub.add_parser("allow").add_argument("address")
    sub.add_parser("announce")
    sub.add_parser("address")
    sub.add_parser("status")
    sub.add_parser("cameras-supported")
    configs = sub.add_parser("write-configs")
    configs.add_argument("--name", default=f"Eyes Of Aziz Bridge {socket.gethostname()}")
    configs.add_argument("--rnode", default="auto", help="auto, none, or a serial port")
    configs.add_argument("--reticulum-dir", default="~/.reticulum")
    configs.add_argument("--lxmd-dir", default="~/.lxmd")
    configs.add_argument("--force", action="store_true", help="overwrite existing configs")
    args = parser.parse_args(argv)

    home = MeshHome.default()
    if args.command == "run":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        from .agent import MeshAgent  # needs rns/lxmf, which only the agent does

        MeshAgent(home).run_forever()
        return 0
    if args.command == "setup":
        return _setup(home, args)
    if args.command == "send":
        try:
            address = normalize_address(args.address)
        except ValueError as exc:
            print(exc)
            return 2
        home.queue({"to": address, "text": " ".join(args.text)})
        print("Queued. Check with: eyes-of-aziz-mesh sent")
        return 0
    if args.command in ("inbox", "sent"):
        _print_records(home.tail(f"{args.command}.jsonl", args.n))
        return 0
    if args.command == "allow":
        try:
            added = home.allow(args.address)
        except ValueError as exc:
            print(exc)
            return 2
        print("Allowed." if added else "Already allowed.")
        return 0
    if args.command == "announce":
        home.queue({"announce": True})
        return 0
    if args.command == "address":
        address = home.read_text("address")
        print(address or "Not started yet - the agent writes it on its first run.")
        return 0 if address else 1
    if args.command == "status":
        status = home.read_json("status.json")
        if not status:
            print("No status yet - is the eyes-of-aziz-mesh service running?")
            return 1
        for key, value in status.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "cameras-supported":
        model = hardware.read_model() or "unknown machine"
        supported = hardware.this_machine_supports_cameras()
        print(f"{model}: {'can' if supported else 'cannot'} run the camera bridge")
        return 0 if supported else 1
    if args.command == "write-configs":
        return _write_configs(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
