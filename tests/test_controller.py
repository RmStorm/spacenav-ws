import asyncio
import pytest

from spacenav_ws.controller import Controller, filter_motion_sample
from spacenav_ws.main import nlproxy_info
from spacenav_ws.runtime import set_public_endpoint
from spacenav_ws.types import MotionAxesMode, MotionSample, NavigationMode
from spacenav_ws.wamp import Prefix, WampSession


class DummyWebSocket:
    async def accept(self, subprotocol=None):
        return None

    async def send_json(self, payload):
        return None


class DummyReader:
    async def readexactly(self, n):
        raise NotImplementedError


def make_controller(client_metadata=None):
    reader = DummyReader()
    session = WampSession(DummyWebSocket())
    return Controller(
        reader=reader,
        session=session,
        client_metadata=client_metadata or {"name": "Onshape", "version": 0.6},
    )


def make_active_controller():
    controller = make_controller()
    controller.focus = True
    controller.subscribed = True
    return controller


def test_zero_motion_sample_stops_motion():
    controller = make_active_controller()
    calls = []

    async def fake_set_motion(active: bool):
        calls.append(active)

    controller.motion_active = True
    controller.bridge.set_motion = fake_set_motion

    asyncio.run(controller._handle_motion(MotionSample(tx=0, ty=0, tz=0, rx=0, ry=0, rz=0, period_ms=8)))

    assert calls == [False]
    assert controller.motion_active is False


async def _exercise_focus_drop():
    controller = make_controller()
    calls = []

    async def fake_set_motion(active: bool):
        calls.append(active)

    controller.bridge.set_motion = fake_set_motion
    controller.motion_active = True

    await controller.client_update(controller.id, {"focus": False})
    return controller, calls


def test_focus_drop_stops_motion():
    controller, calls = asyncio.run(_exercise_focus_drop())

    assert calls == [False]
    assert controller.motion_active is False


async def _exercise_idle_stop():
    controller = make_controller()
    calls = []

    async def fake_set_motion(active: bool):
        calls.append(active)

    controller.bridge.set_motion = fake_set_motion
    controller.motion_active = True
    controller.last_motion_at = 0.0
    controller.stop_idle_timeout_s = 0.01

    task = asyncio.create_task(controller._stop_idle_loop())
    try:
        await asyncio.sleep(0.03)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return controller, calls


def test_quiescence_stops_motion():
    controller, calls = asyncio.run(_exercise_idle_stop())

    assert calls == [False]
    assert controller.motion_active is False


def test_each_motion_sample_reads_fresh_navigation_state():
    controller = make_active_controller()
    event = MotionSample(tx=1, ty=0, tz=0, rx=0, ry=0, rz=0, period_ms=8)
    calls = []

    async def fake_set_motion(active: bool):
        calls.append(("motion", active))

    async def fake_read_navigation_state():
        calls.append(("read", None))
        raise RuntimeError("stop after fresh state read")

    controller.bridge.set_motion = fake_set_motion
    controller.bridge.read_navigation_state = fake_read_navigation_state

    try:
        asyncio.run(controller._handle_motion(event))
    except RuntimeError as exc:
        assert str(exc) == "stop after fresh state read"

    assert calls == [("motion", True), ("read", None)]


def test_missing_pivot_stops_motion_without_crashing():
    controller = make_active_controller()
    calls = []

    async def fake_set_motion(active: bool):
        calls.append(active)

    async def fake_read_navigation_state():
        from spacenav_ws.onshape_bridge import PivotUnavailableError

        raise PivotUnavailableError("Onshape bridge did not provide pivot.position")

    controller.bridge.set_motion = fake_set_motion
    controller.bridge.read_navigation_state = fake_read_navigation_state

    asyncio.run(controller._handle_motion(MotionSample(tx=1, ty=0, tz=0, rx=0, ry=0, rz=0, period_ms=8)))

    assert calls == [True, False]
    assert controller.motion_active is False


@pytest.mark.parametrize(
    ("motion_active", "period_ms", "expected_period_ms"),
    [
        (False, 1234, 0),
        (True, 42, 42),
    ],
)
def test_motion_dt_uses_zero_only_for_gesture_start(monkeypatch, motion_active, period_ms, expected_period_ms):
    controller = make_active_controller()
    controller.motion_active = motion_active
    event = MotionSample(tx=1, ty=0, tz=0, rx=0, ry=0, rz=0, period_ms=period_ms)
    seen = {}

    async def fake_read_navigation_state():
        return object()

    async def fake_write_navigation_state(next_state, previous_state=None):
        return None

    async def fake_set_motion(active: bool):
        return None

    controller.bridge.set_motion = fake_set_motion
    controller.bridge.read_navigation_state = fake_read_navigation_state
    controller.bridge.write_navigation_state = fake_write_navigation_state

    from spacenav_ws import controller as controller_module

    def fake_apply_motion(state, sample, config, mode):
        seen["period_ms"] = sample.period_ms
        seen["mode"] = mode
        raise RuntimeError("stop after apply_motion capture")

    monkeypatch.setattr(controller_module, "apply_motion_with_mode", fake_apply_motion)
    with pytest.raises(RuntimeError, match="stop after apply_motion capture"):
        asyncio.run(controller._handle_motion(event))

    assert seen["period_ms"] == expected_period_ms
    assert seen["mode"] is NavigationMode.OBJECT


@pytest.mark.parametrize(
    ("button_id", "expected_modes", "attr_name"),
    [
        (0, [NavigationMode.TARGET_CAMERA, NavigationMode.OBJECT], "mode"),
        (
            1,
            [MotionAxesMode.ROTATION_ONLY, MotionAxesMode.TRANSLATION_ONLY, MotionAxesMode.ALL],
            "axes_mode",
        ),
    ],
)
def test_supported_button_actions_cycle_expected_modes(button_id, expected_modes, attr_name):
    controller = make_active_controller()

    for expected in expected_modes:
        asyncio.run(controller._handle_button(button_id))
        assert getattr(controller, attr_name) is expected


def test_filter_motion_sample_zeroes_disabled_axes():
    sample = MotionSample(tx=1, ty=2, tz=3, rx=4, ry=5, rz=6, period_ms=7)

    rotation_only = filter_motion_sample(sample, MotionAxesMode.ROTATION_ONLY)
    assert rotation_only == MotionSample(tx=0, ty=0, tz=0, rx=4, ry=5, rz=6, period_ms=7)

    translation_only = filter_motion_sample(sample, MotionAxesMode.TRANSLATION_ONLY)
    assert translation_only == MotionSample(tx=1, ty=2, tz=3, rx=0, ry=0, rz=0, period_ms=7)


def test_button_toggle_does_not_require_focus_or_subscription():
    controller = make_controller()

    asyncio.run(controller._handle_button(0))
    assert controller.mode is NavigationMode.TARGET_CAMERA


def test_bridge_info_uses_configured_public_port():
    set_public_endpoint("127.51.68.120", 9443)
    assert asyncio.run(nlproxy_info())["port"] == 9443


def test_controller_uses_unresolved_uris_until_prefixes_arrive():
    controller = make_controller()

    assert controller.controller_uri == "3dconnexion:3dcontroller/controller0"
    assert "3dx_rpc:update" in controller.session.wamp.call_handlers


def test_controller_uses_negotiated_wamp_prefixes():
    session = WampSession(DummyWebSocket())
    asyncio.run(session.wamp.handle_prefix(Prefix("3dx_rpc", "wss://127.51.68.120/3dconnexion#")))
    asyncio.run(session.wamp.handle_prefix(Prefix("3dconnexion", "wss://127.51.68.120/3dconnexion")))

    controller = Controller(
        reader=DummyReader(),
        session=session,
        client_metadata={"name": "Onshape", "version": 0.6},
    )

    assert controller.controller_uri == "wss://127.51.68.120/3dconnexion3dcontroller/controller0"
    assert "wss://127.51.68.120/3dconnexion#update" in controller.session.wamp.call_handlers
