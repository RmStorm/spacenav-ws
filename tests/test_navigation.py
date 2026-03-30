import numpy as np
import pytest

from spacenav_ws.navigation import (
    NavigationConfig,
    apply_motion,
    apply_motion_with_mode,
    camera_state_from_affine,
    camera_state_to_affine,
    model_input_to_model_output,
    model_output_to_onshape,
    motion_activity,
    motion_sample_to_model_input,
    orthonormalize,
    parse_remap,
    remap_device_axes,
)
from spacenav_ws.types import CameraState, MotionSample, NavigationMode, NavigationState


def make_camera(*, perspective: bool = True, extents=None):
    rotation = np.eye(3, dtype=float)
    position = np.array([0.0, 0.0, 10.0], dtype=float)
    frustum = np.array([-1.0, 1.0, -0.75, 0.75, 1.0, 100.0], dtype=float) if perspective else None
    return CameraState(rotation=rotation, position=position, perspective=perspective, extents=extents, frustum=frustum)


def test_motion_activity_is_raw_after_single_remap():
    sample = MotionSample(tx=10, ty=20, tz=30, rx=40, ry=50, rz=60, period_ms=10)
    config = NavigationConfig()
    remapped_translation, remapped_rotation = remap_device_axes(sample, config.remap)
    assert motion_activity(sample, config) == max(np.max(np.abs(remapped_translation)), np.max(np.abs(remapped_rotation)))


def test_parse_remap_rejects_invalid_specs():
    with pytest.raises(ValueError):
        parse_remap("XYzUW")
    with pytest.raises(ValueError):
        parse_remap("XXXXXX")


def test_perspective_pan_uses_frustum_scaled_pivot_depth():
    camera = make_camera(perspective=True)
    state = NavigationState(camera=camera, pivot=np.array([0.0, 0.0, 0.0]))
    sample = MotionSample(tx=100, ty=100, tz=0, rx=0, ry=0, rz=0, period_ms=16)
    config = NavigationConfig()
    translation_camera, _angular_camera, _zoom_rate, dt = motion_sample_to_model_input(state, sample, config)

    assert dt == 0.016
    # depth = 10, frustum spans at near=1 are 2.0 x 1.5, so spans at depth are 20 x 15
    expected = np.array([20.0, 15.0], dtype=float) * (100.0 * config.pan_rate)
    assert np.allclose(np.abs(translation_camera[:2]), expected, atol=1e-9)
    assert translation_camera[2] == 0.0


def test_perspective_pan_moves_camera_without_rotating():
    state = NavigationState(camera=make_camera(), pivot=np.zeros(3))
    sample = MotionSample(tx=250, ty=0, tz=0, rx=0, ry=0, rz=0, period_ms=20)

    result = apply_motion(state, sample, NavigationConfig())

    assert not np.allclose(result.camera.position, state.camera.position)
    assert np.allclose(result.camera.rotation, state.camera.rotation)


def test_orbit_keeps_distance_to_pivot():
    state = NavigationState(camera=make_camera(), pivot=np.zeros(3))
    sample = MotionSample(tx=0, ty=0, tz=0, rx=220, ry=180, rz=0, period_ms=20)

    result = apply_motion(state, sample, NavigationConfig())

    before = np.linalg.norm(state.camera.position - state.pivot)
    after = np.linalg.norm(result.camera.position - result.pivot)
    assert np.isclose(before, after)
    assert np.allclose(result.camera.rotation @ result.camera.rotation.T, np.eye(3), atol=1e-6)


def test_translation_uses_camera_local_basis():
    rotation = np.array(
        [
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 0.0],
        ],
        dtype=float,
    )
    camera = CameraState(
        rotation=rotation,
        position=np.array([10.0, 0.0, 0.0]),
        perspective=True,
        extents=None,
        frustum=np.array([-1.0, 1.0, -0.75, 0.75, 1.0, 100.0], dtype=float),
    )
    state = NavigationState(camera=camera, pivot=np.zeros(3))
    sample = MotionSample(tx=250, ty=0, tz=0, rx=0, ry=0, rz=0, period_ms=20)

    result = apply_motion(state, sample, NavigationConfig())

    delta = result.camera.position - state.camera.position
    expected_direction = -camera.rotation[:, 0]
    assert np.dot(delta, expected_direction) > 0
    assert np.linalg.norm(np.cross(delta, expected_direction)) < 1e-6


def test_orthographic_zoom_scales_extents_multiplicatively():
    extents = np.array([-2.0, -1.0, -100.0, 2.0, 1.0, 100.0], dtype=float)
    camera = make_camera(perspective=False, extents=extents)
    state = NavigationState(camera=camera, pivot=np.zeros(3))
    sample = MotionSample(tx=0, ty=0, tz=260, rx=0, ry=0, rz=0, period_ms=20)

    result = apply_motion(state, sample, NavigationConfig())

    assert result.camera.extents is not None
    assert np.abs(result.camera.extents[0]) > np.abs(extents[0])
    assert np.abs(result.camera.extents[1]) > np.abs(extents[1])
    assert np.abs(result.camera.extents[3]) > np.abs(extents[3])
    assert np.abs(result.camera.extents[4]) > np.abs(extents[4])
    assert np.isclose(result.camera.extents[2], extents[2])
    assert np.isclose(result.camera.extents[5], extents[5])


def test_orthonormalize_repairs_small_drift():
    drifted = np.array([[1.0, 0.01, 0.0], [0.0, 0.999, -0.03], [0.0, 0.03, 1.001]])

    result = orthonormalize(drifted)

    assert np.allclose(result @ result.T, np.eye(3), atol=1e-6)


def test_apply_motion_matches_three_stage_pipeline():
    state = NavigationState(camera=make_camera(), pivot=np.zeros(3))
    sample = MotionSample(tx=40, ty=-50, tz=60, rx=70, ry=-80, rz=90, period_ms=16)
    config = NavigationConfig()

    via_apply = apply_motion_with_mode(state, sample, config, NavigationMode.OBJECT)
    translation_camera, angular_camera, zoom_rate, dt = motion_sample_to_model_input(state, sample, config)
    rotation_world, translation_world, zoom_scale = model_input_to_model_output(state, translation_camera, angular_camera, zoom_rate, dt)
    via_stages = model_output_to_onshape(state, rotation_world, translation_world, zoom_scale, NavigationMode.OBJECT)

    assert np.allclose(via_apply.camera.rotation, via_stages.camera.rotation)
    assert np.allclose(via_apply.camera.position, via_stages.camera.position)


def test_target_camera_rotation_is_about_pivot():
    state = NavigationState(camera=make_camera(), pivot=np.array([50.0, -25.0, 10.0], dtype=float))
    sample = MotionSample(tx=0, ty=0, tz=0, rx=220, ry=0, rz=0, period_ms=20)

    result = apply_motion_with_mode(state, sample, NavigationConfig(), NavigationMode.TARGET_CAMERA)

    assert not np.allclose(result.camera.position, state.camera.position)
    assert not np.allclose(result.camera.rotation, state.camera.rotation)


def test_target_camera_forward_push_moves_camera_forward():
    state = NavigationState(camera=make_camera(), pivot=np.zeros(3))
    sample = MotionSample(tx=0, ty=0, tz=250, rx=0, ry=0, rz=0, period_ms=20)

    result = apply_motion_with_mode(state, sample, NavigationConfig(), NavigationMode.TARGET_CAMERA)

    assert result.camera.position[2] < state.camera.position[2]


def test_live_onshape_perspective_example_round_trips():
    affine = [
        0.8960101950803042,
        0.09676163696332868,
        0.43336233791635775,
        0.0,
        0.051719840831964015,
        -0.9920673330422971,
        0.11457514902743902,
        0.0,
        0.4410110977928683,
        -0.08024707049182847,
        -0.8939069410744098,
        0.0,
        0.2800609773408067,
        -0.2448826849144665,
        -0.7335550769205457,
        1.0,
    ]
    frustum = [
        -0.25162227012963856,
        0.25162227012963856,
        -0.28778816216662023,
        0.28778816216662023,
        0.6947820841930823,
        0.9066835460694114,
    ]

    camera = camera_state_from_affine(affine, perspective=True, extents=None, frustum=frustum)
    result = camera_state_to_affine(camera)

    assert np.allclose(result, affine)
    assert np.allclose(camera.frustum, frustum)
