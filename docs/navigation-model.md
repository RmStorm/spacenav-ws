# Navigation Model

Normative math for SpaceMouse input to Onshape camera updates.

`dt = period_ms / 1000`, where `period_ms` comes from `spacenavd` / `libspnav`.

## Input

Raw device sample:

`m = [tx, ty, tz, rx, ry, rz]^T`

Adapter remap:

`u = S m`

where `S` is a signed permutation encoded by the six-character `remap` string.

Default `remap = XYzUWV`:

- translation channels: `x y z`
- rotation channels: `u v w`
- uppercase: positive raw axis
- lowercase: negative raw axis

Rate model:

`v_c = diag(k_pan, k_pan, k_zoom) [u_tx, u_ty, u_tz]^T`

`ω_c = k_ang [u_rx, u_ry, u_rz]^T`

Camera-frame axes:

- `+x`: screen right
- `+y`: screen up
- `+z`: `-forward`

So positive zoom input means "object closer / camera forward", which is motion
along `-z` in the camera frame.

## Camera Pose

Onshape `view.affine` is a flat 16-element column-major matrix:

`M = [r u -f e]`

where:

- `r`: camera right in world coordinates
- `u`: camera up in world coordinates
- `f`: camera forward in world coordinates
- `e`: camera eye position in world coordinates

Equivalently, `R = [r u -f]` is the world-from-camera rotation used by the adapter.

## Pivot Depth And Pan Scale

Let `c` be the current pivot.

Pivot depth:

`d = |f^T (c - e)|`

Perspective frustum:

`view.frustum = [left, right, bottom, top, near, far]`

Perspective screen spans at depth `d`:

`span_x = (right - left) d / near`

`span_y = (top - bottom) d / near`

Orthographic spans:

`span_x = right - left`

`span_y = top - bottom`

## Camera-Frame Increment

Translation increment in camera coordinates:

`Δt_c = [span_x α_x, span_y α_y, δ_dolly]^T dt`

Incremental camera-frame rotation:

`ΔR_c = Exp(dt [ω_c]x)`

World-space translation:

`Δt = R Δt_c`

where `R = [r u -f]`.

## Object Mode

Object mode applies the inverse camera transform about pivot `c`.

World rotation:

`ΔR_w = R ΔR_c R^T`

Camera update:

`R' = ΔR_w^T R = R ΔR_c^T`

`e' = c + ΔR_w^T (e - c - Δt)`

## Target-Camera Mode

Target-camera mode applies direct camera motion about pivot `c`.

Camera update:

`R' = ΔR_w R = R ΔR_c`

`e' = c + ΔR_w (e - c + Δt)`

## Zoom

### Perspective

Perspective zoom is dolly:

`δ_dolly = d δ / 6`

### Orthographic

Orthographic zoom rescales x/y extents about their center.

`view.extents = [left, bottom, -far, right, top, -near]`

Only `left`, `right`, `bottom`, and `top` participate in zoom.

Scale law:

`s = 2^{-δ/6}`

Half-span update:

`hx' = s hx`

`hy' = s hy`

## Source Notes

- `spacenavd` writes motion `period` in milliseconds.
- `libspnav` exposes the same field as `spnav_event_motion.period`.
