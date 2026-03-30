from __future__ import annotations

from dataclasses import dataclass
from math import log

import numpy as np

from spacenav_ws.types import CameraState, MotionSample, NavigationMode, NavigationState

EPSILON = 1e-9
MAX_COUNT = 350.0
DEFAULT_REMAP = "XYzUWV"


@dataclass(frozen=True)
class NavigationConfig:
    # Screen-plane pan rate, in view spans / second / raw device count.
    pan_rate: float = 3 / MAX_COUNT
    # Onshape's zoom law is base-2: scale = 2^(-delta / 6). The log(2) converts
    # between that base-2 zoom delta and the natural exponential/log form used
    # by the adapter's internal scalar rate.
    zoom_rate: float = 20 / log(2.0) / MAX_COUNT
    # Angular rates, in rad / second / raw device count.
    angular_rate: float = 5 / MAX_COUNT
    # Signed raw-axis permutation. First 3 chars map model translation x/y/z.
    # Last 3 map model rotation u/v/w.
    # Uppercase = positive, lowercase = negative.
    remap: str = DEFAULT_REMAP


def parse_remap(remap: str) -> tuple[tuple[int, int], ...]:
    if len(remap) != 6:
        raise ValueError(f"Expected 6 remap characters, got {len(remap)}")
    normalized = remap.upper()
    if sorted(normalized) != sorted("XYZUVW"):
        raise ValueError("Remap must contain each of x,y,z,u,v,w exactly once, with case indicating sign")
    index_by_axis = {axis: index for index, axis in enumerate("XYZUVW")}
    return tuple((index_by_axis[char.upper()], 1 if char.isupper() else -1) for char in remap)


def remap_device_axes(sample: MotionSample, remap: str = DEFAULT_REMAP) -> tuple[np.ndarray, np.ndarray]:
    # Keep spacenavd/libspnav raw semantics untouched and apply the one adapter
    # remap here.
    raw = np.array([sample.tx, sample.ty, sample.tz, sample.rx, sample.ry, sample.rz], dtype=float)
    parsed = parse_remap(remap)
    remapped = np.array([sign * raw[index] for index, sign in parsed], dtype=float)
    translation = remapped[:3]
    rotation = remapped[3:]
    return translation, rotation


def motion_activity(sample: MotionSample, config: NavigationConfig) -> float:
    translation, rotation = remap_device_axes(sample, config.remap)
    return float(max(np.max(np.abs(translation)), np.max(np.abs(rotation))))


def _camera_forward(rotation: np.ndarray) -> np.ndarray:
    return -rotation[:, 2]


def _pivot_depth(camera: CameraState, pivot: np.ndarray, epsilon: float) -> float:
    forward = _camera_forward(camera.rotation)
    return max(abs(float(np.dot(pivot - camera.position, forward))), epsilon)


def _perspective_view_spans(camera: CameraState, depth: float, epsilon: float) -> tuple[float, float]:
    if camera.frustum is None or len(camera.frustum) < 6:
        return depth, depth
    left, right, bottom, top, near, _far = camera.frustum
    near = max(abs(float(near)), epsilon)
    span_x = (float(right) - float(left)) * depth / near
    span_y = (float(top) - float(bottom)) * depth / near
    return max(span_x, epsilon), max(span_y, epsilon)


def orthonormalize(rotation: np.ndarray) -> np.ndarray:
    u, _, vt = np.linalg.svd(rotation)
    result = u @ vt
    if np.linalg.det(result) < 0:
        u[:, -1] *= -1.0
        result = u @ vt
    return result


def rotation_from_axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    axis_norm = np.linalg.norm(axis)
    if axis_norm < 1e-12 or abs(angle) < 1e-12:
        return np.eye(3, dtype=float)

    unit = axis / axis_norm
    x, y, z = unit
    skew = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=float)
    sin_a = np.sin(angle)
    cos_a = np.cos(angle)
    return np.eye(3, dtype=float) + sin_a * skew + (1.0 - cos_a) * (skew @ skew)


def camera_state_from_affine(
    affine: list[float],
    perspective: bool,
    extents: list[float] | None,
    frustum: list[float] | None,
) -> CameraState:
    # Onshape's getFrame()/setFrame() uses a flat column-major array:
    # columns = [right, up, -forward, eye].
    matrix = np.asarray(affine, dtype=float).reshape(4, 4, order="F")
    rotation = orthonormalize(matrix[:3, :3])
    position = matrix[:3, 3].copy()
    return CameraState(
        rotation=rotation,
        position=position,
        perspective=perspective,
        extents=None if extents is None else np.asarray(extents, dtype=float),
        frustum=None if frustum is None else np.asarray(frustum, dtype=float),
    )


def camera_state_to_affine(camera: CameraState) -> list[float]:
    matrix = np.eye(4, dtype=float)
    matrix[:3, :3] = orthonormalize(camera.rotation)
    matrix[:3, 3] = camera.position
    return matrix.reshape(-1, order="F").tolist()


def motion_sample_to_model_input(
    state: NavigationState, sample: MotionSample, config: NavigationConfig
) -> tuple[np.ndarray, np.ndarray, float, float]:
    dt = max(sample.period_ms, 0) / 1000.0
    translation_counts, rotation_counts = remap_device_axes(sample, config.remap)
    camera = state.camera
    zoom_delta = config.zoom_rate * translation_counts[2]

    if camera.perspective:
        depth = _pivot_depth(camera, state.pivot, EPSILON)
        span_x, span_y = _perspective_view_spans(camera, depth, EPSILON)
        translation_camera = np.array(
            [
                config.pan_rate * span_x * translation_counts[0],
                config.pan_rate * span_y * translation_counts[1],
                depth * zoom_delta / 6.0,
            ],
            dtype=float,
        )
    else:
        extents = camera.extents if camera.extents is not None else np.array([-1.0, -1.0, -1.0, 1.0, 1.0, 1.0], dtype=float)
        span_x = max(extents[3] - extents[0], EPSILON)
        span_y = max(extents[4] - extents[1], EPSILON)
        translation_camera = np.array(
            [
                config.pan_rate * span_x * translation_counts[0],
                config.pan_rate * span_y * translation_counts[1],
                0.0,
            ],
            dtype=float,
        )

    angular_camera = config.angular_rate * rotation_counts
    return translation_camera, angular_camera, zoom_delta, dt


def model_input_to_model_output(
    state: NavigationState,
    translation_camera: np.ndarray,
    angular_camera: np.ndarray,
    zoom_rate: float,
    dt: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    rotation_world_from_camera = orthonormalize(state.camera.rotation)
    delta_rotation_camera = rotation_from_axis_angle(
        angular_camera,
        np.linalg.norm(angular_camera) * dt,
    )
    rotation_world = rotation_world_from_camera @ delta_rotation_camera @ rotation_world_from_camera.T
    translation_world = rotation_world_from_camera @ (translation_camera * dt)
    zoom_scale = 2.0 ** (-(zoom_rate * dt) / 6.0)
    return rotation_world, translation_world, zoom_scale


def model_output_to_onshape(
    state: NavigationState,
    rotation_world: np.ndarray,
    translation_world: np.ndarray,
    zoom_scale: float,
    mode: NavigationMode,
) -> NavigationState:
    camera = state.camera
    rotation = orthonormalize(camera.rotation)
    position = camera.position.copy()
    pivot = state.pivot.copy()

    if mode is NavigationMode.OBJECT:
        rotation = rotation_world.T @ rotation
        position = pivot + rotation_world.T @ (position - pivot - translation_world)
    else:
        rotation = rotation_world @ rotation
        position = pivot + rotation_world @ (position - pivot + translation_world)

    rotation = orthonormalize(rotation)
    extents = camera.extents
    if not camera.perspective and extents is not None and not np.isclose(zoom_scale, 1.0):
        extents = extents.copy()
        center_x = 0.5 * (extents[0] + extents[3])
        center_y = 0.5 * (extents[1] + extents[4])
        half_x = 0.5 * (extents[3] - extents[0]) * zoom_scale
        half_y = 0.5 * (extents[4] - extents[1]) * zoom_scale
        extents[0] = center_x - half_x
        extents[3] = center_x + half_x
        extents[1] = center_y - half_y
        extents[4] = center_y + half_y

    return NavigationState(
        camera=CameraState(
            rotation=rotation,
            position=position,
            perspective=camera.perspective,
            extents=extents,
            frustum=camera.frustum,
        ),
        pivot=pivot,
    )


def apply_motion(state: NavigationState, sample: MotionSample, config: NavigationConfig) -> NavigationState:
    return apply_motion_with_mode(state, sample, config, NavigationMode.OBJECT)


def apply_motion_with_mode(
    state: NavigationState,
    sample: MotionSample,
    config: NavigationConfig,
    mode: NavigationMode,
) -> NavigationState:
    translation_camera, angular_camera, zoom_rate, dt = motion_sample_to_model_input(state, sample, config)
    if not np.any(translation_camera) and not np.any(angular_camera) and np.isclose(zoom_rate, 0.0):
        return state
    rotation_world, translation_world, zoom_scale = model_input_to_model_output(state, translation_camera, angular_camera, zoom_rate, dt)
    return model_output_to_onshape(state, rotation_world, translation_world, zoom_scale, mode)
