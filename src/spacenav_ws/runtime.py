from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "spacenav-ws"
STATE_DIR_ENV = "SPACENAV_WS_STATE_DIR"
PUBLIC_HOST_ENV = "SPACENAV_WS_PUBLIC_HOST"
PUBLIC_PORT_ENV = "SPACENAV_WS_PUBLIC_PORT"
SYSTEM_STATE_DIR = Path("/var/lib") / APP_NAME


def set_state_dir_override(path: Path | None) -> None:
    if path is None:
        return
    os.environ[STATE_DIR_ENV] = str(path.expanduser().resolve())


def set_public_endpoint(host: str, port: int) -> None:
    os.environ[PUBLIC_HOST_ENV] = host
    os.environ[PUBLIC_PORT_ENV] = str(port)


def public_host() -> str:
    return os.environ.get(PUBLIC_HOST_ENV, "127.51.68.120")


def public_port() -> int:
    return int(os.environ.get(PUBLIC_PORT_ENV, "8181"))


def bridge_ws_origin() -> str:
    return f"wss://{public_host()}:{public_port()}"


def state_dir() -> Path:
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return SYSTEM_STATE_DIR
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return (Path(xdg_state_home).expanduser() / APP_NAME).resolve()
    return (Path.home() / ".local" / "state" / APP_NAME).resolve()


def certs_dir() -> Path:
    return state_dir() / "certs"
