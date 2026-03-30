import asyncio

import numpy as np
import pytest

from spacenav_ws.onshape_bridge import OnshapeBridge, PivotUnavailableError
from spacenav_ws.types import CameraState


class DummySession:
    def __init__(self, responses):
        self.responses = responses

    async def client_rpc(self, controller_uri, method, key):
        value = self.responses.get(key)
        if isinstance(value, BaseException):
            raise value
        return value


def make_camera(*, extents=None):
    return CameraState(
        rotation=np.eye(3, dtype=float),
        position=np.array([1.0, 2.0, 30.0], dtype=float),
        perspective=True,
        extents=extents,
        frustum=None,
    )


def make_bridge(responses):
    return OnshapeBridge(DummySession(responses), "wss://bridge/controller")


def test_read_pivot_prefers_pivot_position():
    bridge = make_bridge({"pivot.position": [7.0, 8.0, 9.0]})

    pivot = asyncio.run(bridge.read_pivot(make_camera()))

    assert np.allclose(pivot, [7.0, 8.0, 9.0])


def test_read_pivot_raises_when_pivot_position_is_missing():
    bridge = make_bridge({"pivot.position": None})

    with pytest.raises(PivotUnavailableError, match="pivot.position"):
        asyncio.run(bridge.read_pivot(make_camera(extents=None)))
