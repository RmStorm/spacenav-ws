import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path

import typer
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from rich.logging import RichHandler

from spacenav_ws.controller import create_mouse_controller
from spacenav_ws.debug_page import ONSHAPE_USER_SCRIPT, render_debug_page
from spacenav_ws.navigation import DEFAULT_REMAP, NavigationConfig, parse_remap
from spacenav_ws.raw_input import PACKET_SIZE, SpacenavConnectionError, decode_packet, open_spacenav_connection
from spacenav_ws.runtime import public_port, set_public_endpoint, set_state_dir_override, state_dir
from spacenav_ws.tls import ensure_self_signed_cert, resolve_tls_paths
from spacenav_ws.wamp import WampSession
from starlette.websockets import WebSocketDisconnect

LOG_LEVEL = os.environ.get("SPACENAV_WS_LOG_LEVEL", "INFO").upper()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SRC_DIR = PROJECT_ROOT / "src"

# TODO: This handler isn't used for the uvicorn logs and I can't be bothered finding the magic logging incantations to make it so.
logging.basicConfig(level=LOG_LEVEL, format="%(message)s", datefmt="[%X]", handlers=[RichHandler()])

ORIGINS = [
    "https://127.51.68.120",
    "https://127.51.68.120:8181",
    "https://3dconnexion.com",
    "https://cad.onshape.com",
]

cli = typer.Typer()
cert_cli = typer.Typer(help="Manage TLS certificates.")
cli.add_typer(cert_cli, name="cert")
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "OPTIONS"], allow_headers=["*"])


@app.get("/3dconnexion/nlproxy")
async def nlproxy_info():
    """HTTP info endpoint for the 3Dconnexion client. Returns which port the WAMP bridge will use and its version."""
    return {"port": public_port(), "version": "1.4.8.21486"}


@app.get("/")
def smoke_test_page(request: Request):
    """Interactive debug page for smoke tests and deployment instructions."""
    host = request.url.hostname or "127.51.68.120"
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return HTMLResponse(
        content=render_debug_page(
            host=host,
            port=port,
            user_script_url=str(request.url_for("onshape_user_script")),
        ),
        status_code=200,
    )


@app.get("/debug/onshape.user.js")
async def onshape_user_script():
    """Userscript that spoofs navigator.platform for Onshape."""
    return PlainTextResponse(ONSHAPE_USER_SCRIPT, media_type="text/javascript")


async def iter_mouse_events(reader, writer):
    try:
        while True:
            yield f"data: {decode_packet(await reader.readexactly(PACKET_SIZE))}\n\n"
    except asyncio.IncompleteReadError:
        logging.debug("spacenav event stream ended")
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


@app.get("/events")
async def mouse_event_stream():
    """Stream mouse motion data."""
    try:
        reader, writer = await open_spacenav_connection()
    except SpacenavConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return StreamingResponse(iter_mouse_events(reader, writer), media_type="text/event-stream")


@app.websocket("/")
async def bridge_websocket(ws: WebSocket):
    """WebSocket endpoint for browser clients that speak the nlproxy WAMP protocol."""
    wamp_session = WampSession(ws)
    try:
        spacenav_reader, _ = await open_spacenav_connection()
    except SpacenavConnectionError:
        await ws.close(code=1011, reason="spacenav unavailable")
        return
    remap = os.environ.get("SPACENAV_WS_REMAP", DEFAULT_REMAP)
    controller = await create_mouse_controller(wamp_session, spacenav_reader, nav_config=NavigationConfig(remap=remap))
    # TODO, better error handling then just dropping the websocket disconnect on the floor?
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(controller.start_mouse_event_stream(), name="mouse")
            tg.create_task(controller.session.start_wamp_message_stream(), name="wamp")
    except* WebSocketDisconnect:
        logging.info("Browser client disconnected")
    except* asyncio.IncompleteReadError:
        logging.info("spacenav stream ended")


@cli.command()
def serve(
    host: str = "127.51.68.120",
    port: int = 8181,
    hot_reload: bool = False,
    remap: str = DEFAULT_REMAP,
    cert_file: Path | None = None,
    key_file: Path | None = None,
    state_root: Path | None = None,
):
    """Start the server that sends spacenav to browser-based applications like Onshape."""
    parse_remap(remap)
    set_state_dir_override(state_root)
    set_public_endpoint(host, port)
    os.environ["SPACENAV_WS_REMAP"] = remap
    try:
        resolved_cert_file, resolved_key_file = resolve_tls_paths(host, cert_file, key_file)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    logging.warning("Navigate to: https://%s:%s You should be prompted to add the cert as an exception to your browser.", host, port)
    logging.info("Runtime state dir: %s", state_dir())
    uvicorn.run(
        "spacenav_ws.main:app",
        host=host,
        port=port,
        ws="auto",
        ssl_certfile=resolved_cert_file,
        ssl_keyfile=resolved_key_file,
        log_level=LOG_LEVEL.lower(),
        reload=hot_reload,
        reload_dirs=[str(PROJECT_SRC_DIR)] if hot_reload else None,
    )


@cert_cli.command("ensure")
def ensure_cert(
    host: str = "127.51.68.120",
    state_root: Path | None = None,
):
    """Create or reuse a managed self-signed certificate for the bridge."""
    set_state_dir_override(state_root)
    cert_file, key_file = ensure_self_signed_cert(host)
    typer.echo(f"cert: {cert_file}")
    typer.echo(f"key:  {key_file}")


@cli.command("read-mouse")
def read_mouse_events():
    """Echo raw decoded events from the spacenav socket."""

    async def log_mouse_stream():
        try:
            reader, writer = await open_spacenav_connection()
        except SpacenavConnectionError as exc:
            logging.error(str(exc))
            raise typer.Exit(1) from exc
        logging.info("Start moving your mouse!")
        async for event in iter_mouse_events(reader, writer):
            logging.info(event.strip())

    asyncio.run(log_mouse_stream())


if __name__ == "__main__":
    cli()
