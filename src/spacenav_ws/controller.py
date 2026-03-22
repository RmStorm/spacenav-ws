import asyncio
import logging
import struct
import time
from typing import Any

import numpy as np
from scipy.spatial import transform

from spacenav_ws.spacenav import MotionEvent, ButtonEvent, from_message
from spacenav_ws.wamp import WampSession, Prefix, Call, Subscribe, CallResult


class Mouse3d:
    """This bad boy doesn't do a damn thing right now!"""

    def __init__(self):
        self.id = "mouse0"


class Controller:
    """Manage shared state and event streaming between a local 3D mouse and a remote client.

    This class subscribes clients over WAMP, tracks focus/subscription state,
    reads raw 3D mouse data from an asyncio.StreamReader, and forwards
    MotionEvent/ButtonEvent updates back to the client via RPC. It also
    provides utility methods for affine‐pivot calculations and generic
    remote_read/write operations.

    Args:
        reader (asyncio.StreamReader):
            Asynchronous stream reader for receiving raw 3D mouse packets.
        _ (Mouse3d):
            Doesn’t do anything.. things should be restructured so that it does probably.
        wamp_state_handler (WampSession):
            WAMP session handler that manages subscriptions and RPC calls.
        client_metadata (dict):
            Metadata about the connected client (e.g. its name and capabilities).

    Attributes:
        id (str):
            Unique identifier for this controller instance (defaults to "controller0").
        client_metadata (dict):
            Same as the constructor arg: information about the client.
        reader (asyncio.StreamReader):
            Stream reader for incoming mouse event bytes.
        wamp_state_handler (WampSession):
            WAMP session object for subscribing and remote RPC.
        subscribed (bool):
            True once the client has subscribed to this controller’s URI.
        focus (bool):
            True when this controller is in focus and should send events.
    """

    CACHE_REFRESH_INTERVAL = 0.5  # seconds between slow-state refreshes

    def __init__(self, reader: asyncio.StreamReader, _: Mouse3d, wamp_state_handler: WampSession, client_metadata: dict):
        self.id = "controller0"
        self.client_metadata = client_metadata
        self.reader = reader
        self.wamp_state_handler = wamp_state_handler

        self.wamp_state_handler.wamp.subscribe_handlers[self.controller_uri] = self.subscribe
        self.wamp_state_handler.wamp.call_handlers["wss://127.51.68.120/3dconnexion#update"] = self.client_update

        self.subscribed = False
        self.focus = False

        # Cached slow-changing state (refreshed periodically, not every event)
        self._cached_model_extents: list | None = None
        self._cached_perspective: bool | None = None
        self._cached_extents: list | None = None
        self._cache_time: float = 0.0

    async def subscribe(self, msg: Subscribe):
        """When a subscription request for self.controller_uri comes in we start broadcasting!"""
        logging.info("handling subscribe %s", msg)
        self.subscribed = True
        self.focus = True

    async def client_update(self, controller_id: str, args: dict[str, Any]):
        # TODO Maybe use some more of this data that the client sends our way?
        logging.debug("Got update for '%s': %s, THESE ARE DROPPED FOR NOW!", controller_id, args)
        if (focus := args.get("focus")) is not None:
            self.focus = focus

    @property
    def controller_uri(self) -> str:
        return f"wss://127.51.68.120/3dconnexion3dcontroller/{self.id}"

    async def remote_write(self, *args):
        return await self.wamp_state_handler.client_rpc(self.controller_uri, "self:update", *args)

    async def remote_read(self, *args):
        return await self.wamp_state_handler.client_rpc(self.controller_uri, "self:read", *args)

    async def _refresh_cache(self):
        """Fetch slow-changing state from the client, concurrently."""
        self._cached_model_extents, self._cached_perspective, self._cached_extents = await asyncio.gather(
            self.remote_read("model.extents"),
            self.remote_read("view.perspective"),
            self.remote_read("view.extents"),
        )
        self._cache_time = time.monotonic()

    async def start_mouse_event_stream(self):
        """Read spacenav events, dropping stale ones so only the latest is processed."""
        logging.info("Starting the mouse stream")
        while True:
            mouse_event = await self.reader.read(32)
            if not (self.focus and self.subscribed):
                continue
            # Drain any queued events so we only process the most recent one
            while len(self.reader._buffer) >= 32:
                mouse_event = bytes(self.reader._buffer[:32])
                del self.reader._buffer[:32]

            nums = struct.unpack("iiiiiiii", mouse_event)
            event = from_message(list(nums))
            if self.client_metadata["name"] in ["Onshape", "WebThreeJS Sample"]:
                await self.update_client(event)
            else:
                logging.warning("Unknown client! Cannot send mouse events, client_metadata:%s", self.client_metadata)

    @staticmethod
    def get_affine_pivot_matrices(model_extents):
        min_pt = np.array(model_extents[0:3], dtype=np.float32)
        max_pt = np.array(model_extents[3:6], dtype=np.float32)
        pivot = (min_pt + max_pt) * 0.5

        pivot_pos = np.eye(4, dtype=np.float32)
        pivot_pos[3, :3] = pivot
        pivot_neg = np.eye(4, dtype=np.float32)
        pivot_neg[3, :3] = -pivot
        return pivot_pos, pivot_neg

    async def update_client(self, event: MotionEvent | ButtonEvent):
        """
        This send mouse events over to the client. Currently just a few properties are used but more are avaialable:
        view.target, view.constructionPlane, view.extents, view.affine, view.perspective, model.extents, selection.empty, selection.extents, hit.lookat, views.front

        """
        # Refresh cached slow-changing state periodically (or on first call)
        if self._cached_model_extents is None or (time.monotonic() - self._cache_time) > self.CACHE_REFRESH_INTERVAL:
            await self._refresh_cache()

        model_extents = self._cached_model_extents
        perspective = self._cached_perspective
        extents = self._cached_extents

        if isinstance(event, ButtonEvent):
            front_view = await self.remote_read("views.front")
            await self.remote_write("view.affine", front_view)
            await self.remote_write("view.extents", [c * 1.2 for c in model_extents])
            # Force cache refresh after button press (view changed drastically)
            self._cached_model_extents = None
            return

        # 1) Only read the rapidly-changing affine matrix (the one we actually need every frame)
        curr_affine = np.asarray(await self.remote_read("view.affine"), dtype=np.float32).reshape(4, 4)

        # This (transpose of top left quadrant) is the correct way to get the rotation matrix of the camera but it is unstable.. Either of the below methods works fine though.
        R_cam = curr_affine[:3, :3].T
        # cam2world = np.linalg.inv(curr_affine)
        # R_cam = cam2world[:3, :3]
        U, _, Vt = np.linalg.svd(R_cam)
        R_cam = U @ Vt

        # 2) Seperately calculate rotation and translation matrices
        angles = np.array([event.pitch, event.yaw, -event.roll]) * 0.01
        R_delta_cam = transform.Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
        R_world = R_cam @ R_delta_cam @ R_cam.T

        rot_delta = np.eye(4, dtype=np.float32)
        rot_delta[:3, :3] = R_world

        # 3) Apply rotation around model pivot, then add camera-relative translation.
        #    The translation row (row 3) of the affine is in world space, so we must
        #    rotate cam_trans from camera space into world space via R_cam before adding.
        pivot_pos, pivot_neg = self.get_affine_pivot_matrices(model_extents)
        new_affine = curr_affine @ (pivot_neg @ rot_delta @ pivot_pos)

        extent_scale = sum(extents) / len(extents)  # Average visible extent for zoom-proportional sensitivity
        cam_trans = np.array([-event.x, -event.z, event.y], dtype=np.float32) * 0.000375 * extent_scale
        new_affine[3, :3] += R_cam @ cam_trans

        # 4) Write back changes — fire writes concurrently
        writes = [self.remote_write("motion", True),
                  self.remote_write("view.affine", new_affine.reshape(-1).tolist())]
        if not perspective:
            zoom_delta = event.y * 0.0001
            scale = 1.0 + zoom_delta
            new_extents = [c * scale for c in extents]
            writes.append(self.remote_write("view.extents", new_extents))
            # Keep cached extents in sync so the next frame uses the updated value
            self._cached_extents = new_extents
        await asyncio.gather(*writes)


async def create_mouse_controller(wamp_state_handler: WampSession, spacenav_reader: asyncio.StreamReader) -> Controller:
    """
    This takes in an active websocket wrapped in a wampsession, it consumes the first couple of messages that form a sort of pseudo handshake..
    When all is said is done it returns an active controller!
    """
    await wamp_state_handler.wamp.begin()
    # The first three messages are typically prefix setters!
    msg = await wamp_state_handler.wamp.next_message()
    while isinstance(msg, Prefix):
        await wamp_state_handler.wamp.run_message_handler(msg)
        msg = await wamp_state_handler.wamp.next_message()

    # The first call after the prefixes must be 'create mouse'
    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dmouse"
    mouse = Mouse3d()  # There is really no point to this lol
    logging.info(f'Created 3d mouse "{mouse.id}" for version {msg.args[1]}')
    await wamp_state_handler.wamp.send_message(CallResult(msg.call_id, {"connexion": mouse.id}))

    # And the second call after the prefixes must be 'create controller'
    msg = await wamp_state_handler.wamp.next_message()
    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create" and msg.args[0] == "3dconnexion:3dcontroller" and msg.args[1] == mouse.id
    metadata = msg.args[2]
    controller = Controller(spacenav_reader, mouse, wamp_state_handler, metadata)
    logging.info(f'Created controller "{controller.id}" for mouse "{mouse.id}", for client "{metadata["name"]}", version "{metadata["version"]}"')

    await wamp_state_handler.wamp.send_message(CallResult(msg.call_id, {"instance": controller.id}))
    return controller
