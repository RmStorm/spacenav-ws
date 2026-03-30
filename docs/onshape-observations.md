# Onshape Bridge Specification

Normative interface notes for the page-side 3Dconnexion bridge used by
`spacenav-ws`.

## Scope

Specified here:

- `view.affine`
- `view.perspective`
- `view.extents`
- `view.frustum`
- `pivot.position`
- redraw / motion lifecycle hooks actually used by the adapter

Not specified here:

- raw SpaceMouse remap policy
- adapter button policy
- general WAMP behavior outside the properties and commands used here

## Required Read Properties

- `view.affine`
- `view.perspective`
- `view.extents`
- `view.frustum`
- `pivot.position`

## `view.affine`

`view.affine` is `camera.getFrame()`.

It is a flat 16-element column-major matrix:

`M = [r u -f e]`

where:

- `r`: camera right in world coordinates
- `u`: camera up in world coordinates
- `f`: camera forward in world coordinates
- `e`: camera eye position in world coordinates

Equivalent column layout:

- column `0` = `r`
- column `1` = `u`
- column `2` = `-f`
- column `3` = `e`

Adapter requirements:

- read `view.affine` as column-major
- treat it as the full camera pose
- write updated camera state back in the same format

## `view.perspective`

Mode bit:

- `true`: perspective
- `false`: orthographic

This determines whether z-zoom is dolly or extent scaling.

## `view.extents`

Orthographic extents:

`[left, bottom, -far, right, top, -near]`

Only these entries are used for zoom state:

- `left`
- `right`
- `bottom`
- `top`

Implications:

- orthographic pan/rotation live in `view.affine`
- orthographic zoom lives in `view.extents`
- `extents[2]` and `extents[5]` are clip values, not zoom state

## `view.frustum`

Perspective frustum:

`[left, right, bottom, top, near, far]`

Perspective pan scale at pivot depth `d`:

`span_x(d) = (right - left) d / near`

`span_y(d) = (top - bottom) d / near`

## `pivot.position`

`pivot.position` is the active Spaceball pivot.

Requirements:

- use it as the primary center of rotation
- do not substitute `view.target`
- if it is absent, do not synthesize an arbitrary fallback pivot; skip motion instead

Observed behavior:

- it is dynamic
- it is not fixed at the origin
- it is not generally equal to `view.target`

## Navigation Semantics

### Object Mode

Object mode is inverse camera motion about `pivot.position`.

### Target-Camera Mode

Target-camera mode is direct camera motion about `pivot.position`.

Rules:

- rotate about the pivot
- translate in the current camera frame
- in orthographic mode, z-zoom still uses extent scaling

## Zoom Semantics

### Perspective

Perspective zoom is dolly:

`dolly = distance * delta / 6`

### Orthographic

Orthographic zoom rescales x/y extents:

`scale = 2^(-delta / 6)`

It does not move the camera rigidly.

## Lifecycle Hooks

The adapter relies on:

- `motion`
- `transaction = 0`

Operational requirement:

- set `motion` to follow active Spaceball motion state
- write `transaction = 0` after navigation updates to force redraw

## Verified Equivalences

Page-side checks show that adapter updates match Onshape primitives for:

- object rotation
- object pan
- orthographic zoom
- perspective dolly
