from __future__ import annotations

import asyncio
import logging
import os
import struct
from typing import Union

from spacenav_ws.types import ButtonSample, MotionSample

SPACENAV_SOCKET_PATH_ENV = "SPACENAV_SOCKET_PATH"
DEFAULT_SPACENAV_SOCKET_PATH = "/var/run/spnav.sock"

PACKET_FORMAT = "iiiiiiii"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

RawInputEvent = Union[MotionSample, ButtonSample]


class SpacenavConnectionError(RuntimeError):
    pass


async def open_spacenav_connection() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    socket_path = os.environ.get(SPACENAV_SOCKET_PATH_ENV, DEFAULT_SPACENAV_SOCKET_PATH)
    try:
        return await asyncio.open_unix_connection(socket_path)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        message = f"Space mouse not found at {socket_path}"
        logging.error(message)
        raise SpacenavConnectionError(message) from exc


def decode_packet(packet: bytes) -> RawInputEvent:
    if len(packet) != PACKET_SIZE:
        raise ValueError(f"Expected {PACKET_SIZE} bytes, got {len(packet)}")

    msg_type, a1, a2, a3, a4, a5, a6, a7 = struct.unpack(PACKET_FORMAT, packet)
    # Upstream spacenavd AF_UNIX protocol:
    #   UEV_MOTION  = 0
    #   UEV_PRESS   = 1
    #   UEV_RELEASE = 2
    # Motion packets carry raw axes in fields 1..6 and period_ms in field 7.
    if msg_type == 0:
        return MotionSample(tx=a1, ty=a2, tz=a3, rx=a4, ry=a5, rz=a6, period_ms=a7)
    if msg_type in (1, 2):
        return ButtonSample(button_id=a1, pressed=msg_type == 1)
    raise ValueError(f"Unknown spacenav packet type: {msg_type}")
