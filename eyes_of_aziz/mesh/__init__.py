"""The Eyes Of Aziz Bridge's mesh side: what a Raspberry Pi runs to be a
Reticulum transport node and LXMF messaging point for the Aziz mesh.

- agent: this bridge's LXMF address. Receives messages, sends what
  `eyes-of-aziz-mesh send` queues (falling back to the local propagation
  node when the recipient is offline), answers commands, sends heartbeats.
- lxmd (Reticulum's own daemon) is the bridge's propagation node: it stores
  messages for phones that are offline and hands them over when they sync.
- rnsh (Reticulum's remote shell) for the owner's computers.

None of it needs OpenCV, so a Pi 3 that can't run the camera bridge can
still run all of this (see hardware.cameras_supported).
"""
