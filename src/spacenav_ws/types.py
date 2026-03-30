from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class NavigationMode(str, Enum):
    OBJECT = "object"
    TARGET_CAMERA = "target-camera"


class MotionAxesMode(str, Enum):
    ALL = "all"
    ROTATION_ONLY = "rotation-only"
    TRANSLATION_ONLY = "translation-only"


@dataclass(frozen=True)
class MotionSample:
    tx: int
    ty: int
    tz: int
    rx: int
    ry: int
    rz: int
    period_ms: int
    type: str = "mtn"


@dataclass(frozen=True)
class ButtonSample:
    button_id: int
    pressed: bool
    type: str = "btn"


@dataclass
class CameraState:
    rotation: np.ndarray
    position: np.ndarray
    perspective: bool
    extents: np.ndarray | None
    frustum: np.ndarray | None


@dataclass
class NavigationState:
    camera: CameraState
    pivot: np.ndarray
