from __future__ import annotations

import asyncio
import logging
from dataclasses import astuple, dataclass, field, replace
from time import monotonic
from typing import Any

from spacenav_ws.navigation import NavigationConfig, apply_motion_with_mode, motion_activity
from spacenav_ws.onshape_bridge import OnshapeBridge, PivotUnavailableError
from spacenav_ws.raw_input import PACKET_SIZE, decode_packet
from spacenav_ws.types import ButtonSample, MotionSample, MotionAxesMode, NavigationMode
from spacenav_ws.wamp import Call, CallResult, Prefix, Subscribe, WampSession

SUPPORTED_CLIENT_NAMES = {"Onshape", "WebThreeJS Sample", "web_threejs.html"}
NAVIGATION_MODE_TOGGLE_BUTTON = 0
MOTION_AXES_CYCLE_BUTTON = 1


def motion_log_fields(sample: MotionSample) -> tuple[int, int, int, int, int, int, int]:
    return astuple(sample)[:-1]


def filter_motion_sample(sample: MotionSample, axes_mode: MotionAxesMode) -> MotionSample:
    if axes_mode is MotionAxesMode.ALL:
        return sample
    if axes_mode is MotionAxesMode.ROTATION_ONLY:
        return replace(sample, tx=0, ty=0, tz=0)
    return replace(sample, rx=0, ry=0, rz=0)


@dataclass
class Controller:
    reader: asyncio.StreamReader
    session: WampSession
    client_metadata: dict[str, Any]
    nav_config: NavigationConfig = field(default_factory=NavigationConfig)
    stop_idle_timeout_s: float = 0.2

    def __post_init__(self):
        self.id = "controller0"
        self.subscribed = False
        self.focus = False
        self.bridge = OnshapeBridge(self.session, self.controller_uri)
        self.motion_active = False
        self.last_motion_at = 0.0
        self.latest_motion: MotionSample | None = None
        self.motion_ready = asyncio.Event()
        self.mode = NavigationMode.OBJECT
        self.axes_mode = MotionAxesMode.ALL
        self.session.wamp.subscribe_handlers[self.controller_uri] = self.subscribe
        self.session.wamp.call_handlers[self.session.wamp.resolve("3dx_rpc:update")] = self.client_update

    @property
    def controller_uri(self) -> str:
        return self.session.wamp.resolve(f"3dconnexion:3dcontroller/{self.id}")

    def is_supported_client(self) -> bool:
        return self.client_metadata.get("name") in SUPPORTED_CLIENT_NAMES

    async def subscribe(self, msg: Subscribe):
        logging.info("handling subscribe %s", msg)
        self.subscribed = True
        self.focus = True

    async def client_update(self, controller_id: str, args: dict[str, Any]):
        logging.debug("Got update for '%s': %s", controller_id, args)
        if (focus := args.get("focus")) is not None:
            self.focus = bool(focus)
            if not self.focus:
                await self._stop_motion()

    async def start_mouse_event_stream(self):
        logging.info("Starting the mouse stream")
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._poll_input_loop(), name="input")
            tg.create_task(self._motion_loop(), name="motion")
            tg.create_task(self._stop_idle_loop(), name="stop-idle")

    async def _poll_input_loop(self):
        while True:
            packet = await self.reader.readexactly(PACKET_SIZE)
            event = decode_packet(packet)
            if isinstance(event, MotionSample):
                self.last_motion_at = monotonic()
                self.latest_motion = event
                self.motion_ready.set()
            elif isinstance(event, ButtonSample) and event.pressed:
                await self._handle_button(event.button_id)

    async def _motion_loop(self):
        while True:
            await self.motion_ready.wait()
            self.motion_ready.clear()
            event = self.latest_motion
            self.latest_motion = None
            if event is None:
                continue
            await self._handle_motion(event)

    async def _handle_button(self, button_id: int):
        if not self.is_supported_client():
            return
        if button_id == NAVIGATION_MODE_TOGGLE_BUTTON:
            self.mode = NavigationMode.TARGET_CAMERA if self.mode is NavigationMode.OBJECT else NavigationMode.OBJECT
            logging.info("Switched navigation mode to %s", self.mode.value)
            await self._stop_motion()
            return
        if button_id == MOTION_AXES_CYCLE_BUTTON:
            self.axes_mode = {
                MotionAxesMode.ALL: MotionAxesMode.ROTATION_ONLY,
                MotionAxesMode.ROTATION_ONLY: MotionAxesMode.TRANSLATION_ONLY,
                MotionAxesMode.TRANSLATION_ONLY: MotionAxesMode.ALL,
            }[self.axes_mode]
            logging.info("Switched motion axes mode to %s", self.axes_mode.value)
            await self._stop_motion()

    async def _handle_motion(self, event: MotionSample):
        if not (self.focus and self.subscribed and self.is_supported_client()):
            return

        sample = replace(event, period_ms=max(0, event.period_ms))
        sample = filter_motion_sample(sample, self.axes_mode)
        if motion_activity(sample, self.nav_config) <= 0.0:
            await self._stop_motion()
            return

        gesture_start = not self.motion_active
        if gesture_start:
            logging.debug("Starting motion gesture")
            await self.bridge.set_motion(True)
            self.motion_active = True

        logging.debug("Reading current navigation state")
        try:
            current_state = await self.bridge.read_navigation_state()
        except PivotUnavailableError as exc:
            logging.warning(str(exc))
            await self._stop_motion()
            return
        # spacenavd's motion.period is time since the previous emitted motion event.
        # After idle silence, the first non-zero sample can carry a large period that
        # should not be integrated as active motion. Bootstrap the gesture with dt=0.
        if gesture_start:
            sample = replace(sample, period_ms=0)

        next_state = apply_motion_with_mode(current_state, sample, self.nav_config, self.mode)
        logging.debug(
            "Writing navigation update tx=%s ty=%s tz=%s rx=%s ry=%s rz=%s dt_ms=%s",
            *motion_log_fields(sample),
        )
        await self.bridge.write_navigation_state(next_state, previous_state=current_state)

    async def _stop_idle_loop(self):
        while True:
            await asyncio.sleep(self.stop_idle_timeout_s / 4.0)
            if not self.motion_active:
                continue
            if monotonic() - self.last_motion_at > self.stop_idle_timeout_s:
                await self._stop_motion()

    async def _stop_motion(self):
        if self.motion_active:
            logging.debug("Stopping motion gesture")
            await self.bridge.set_motion(False)
        self.motion_active = False


async def create_mouse_controller(
    session: WampSession,
    spacenav_reader: asyncio.StreamReader,
    nav_config: NavigationConfig | None = None,
) -> Controller:
    async def expect_create(*prefix_args: Any) -> Call:
        msg = await session.wamp.next_message()
        assert isinstance(msg, Call)
        assert msg.proc_uri == "3dx_rpc:create"
        assert tuple(msg.args[: len(prefix_args)]) == prefix_args
        return msg

    await session.wamp.begin()
    msg = await session.wamp.next_message()
    while isinstance(msg, Prefix):
        await session.wamp.run_message_handler(msg)
        msg = await session.wamp.next_message()

    assert isinstance(msg, Call)
    assert msg.proc_uri == "3dx_rpc:create"
    assert tuple(msg.args[:1]) == ("3dconnexion:3dmouse",)
    mouse_id = "mouse0"
    logging.info('Created 3d mouse "%s" for version %s', mouse_id, msg.args[1])
    await session.wamp.send_message(CallResult(msg.call_id, {"connexion": mouse_id}))

    msg = await expect_create("3dconnexion:3dcontroller", mouse_id)
    metadata = msg.args[2]
    controller = Controller(spacenav_reader, session, metadata, nav_config=nav_config or NavigationConfig())
    logging.info(
        'Created controller "%s" for mouse "%s", for client "%s", version "%s"',
        controller.id,
        mouse_id,
        metadata["name"],
        metadata["version"],
    )
    await session.wamp.send_message(CallResult(msg.call_id, {"instance": controller.id}))
    return controller
