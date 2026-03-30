from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from spacenav_ws.navigation import camera_state_from_affine, camera_state_to_affine
from spacenav_ws.types import CameraState, NavigationState
from spacenav_ws.wamp import WampSession


class PivotUnavailableError(RuntimeError):
    pass


@dataclass
class OnshapeBridge:
    session: WampSession
    controller_uri: str

    async def _try_read_property(self, *args):
        try:
            return await self.session.client_rpc(self.controller_uri, "self:read", *args)
        except Exception:
            return None

    async def read_navigation_state(self) -> NavigationState:
        perspective = bool(await self.session.client_rpc(self.controller_uri, "self:read", "view.perspective"))
        affine = await self.session.client_rpc(self.controller_uri, "self:read", "view.affine")
        extents = await self._try_read_property("view.extents")
        frustum = await self._try_read_property("view.frustum")
        camera = camera_state_from_affine(affine, perspective=perspective, extents=extents, frustum=frustum)
        pivot = await self.read_pivot(camera)
        return NavigationState(camera=camera, pivot=pivot)

    async def read_pivot(self, camera: CameraState) -> np.ndarray:
        value = await self._try_read_property("pivot.position")
        if isinstance(value, list) and len(value) >= 3:
            return np.asarray(value[:3], dtype=float)
        raise PivotUnavailableError("Onshape bridge did not provide pivot.position")

    async def write_navigation_state(self, state: NavigationState, previous_state: NavigationState | None = None):
        await self.session.client_rpc(self.controller_uri, "self:update", "view.affine", camera_state_to_affine(state.camera))
        previous_extents = previous_state.camera.extents if previous_state is not None else None
        extents_changed = state.camera.extents is not None and (previous_extents is None or not np.allclose(state.camera.extents, previous_extents))
        if not state.camera.perspective and extents_changed:
            await self.session.client_rpc(self.controller_uri, "self:update", "view.extents", state.camera.extents.tolist())
        # Onshape only requests another viewer redraw when transaction is reset to 0.
        await self.session.client_rpc(self.controller_uri, "self:update", "transaction", 0)

    async def set_motion(self, active: bool):
        await self.session.client_rpc(self.controller_uri, "self:update", "motion", active)
