# Websockets exposer for the spacenav driver (spacenav‑ws)

![Build Status](https://github.com/rmstorm/spacenav-ws/workflows/Test/badge.svg)
![License](https://img.shields.io/github/license/rmstorm/spacenav-ws)

## Table of Contents

- [About](#about)
- [Changes in this fork](#changes-in-this-fork)
- [Prerequisites](#prerequisites)
- [Usage](#usage)
- [Development](#development)

## About

**spacenav‑ws** is a tiny Python CLI that exposes your 3Dconnexion SpaceMouse over a secure WebSocket, so Onshape on Linux can finally consume it. Under the hood it reverse‑engineers the same traffic Onshape’s Windows client uses and proxies it into your browser.

This lets you use [FreeSpacenav/spacenavd](https://github.com/FreeSpacenav/spacenavd) on Linux with Onshape.

> **Note:** This is a development fork. For the upstream project (which may be available on PyPI), see [rmstorm/spacenav-ws](https://github.com/rmstorm/spacenav-ws).

## Changes in this fork

- **Camera-relative panning**: Panning now follows screen axes regardless of view
  orientation. Previously it moved along fixed world axes. The translation row of
  the affine is in world space, so cam_trans is rotated via R_cam (camera-to-world)
  before applying.
- **Zoom-proportional sensitivity**: Translation scales with view extents so panning
  feels consistent at any zoom level.
- **Input lag reduction**: Cached slow-changing state (model.extents, view.perspective,
  view.extents) with periodic refresh instead of per-event reads. Writes fired
  concurrently via asyncio.gather. Stale spacenav events drained so only the latest
  is processed.
- **Tuned sensitivity**: Rotation, translation, and zoom constants tuned for
  spacenavd sensitivity=1.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A running instance of [spacenavd](https://github.com/FreeSpacenav/spacenavd)
- A modern browser (Chrome/Firefox) with a userscript manager (Tampermonkey/Greasemonkey)

## Usage

1. **Clone the repository**
```bash
git clone https://github.com/Crono141/spacenav-ws.git
cd spacenav-ws
```

2. **Validate spacenavd**
```bash
uv run spacenav-ws read-mouse
# → should print spacemouse events
```

3. **Run the server and trust the cert**
```bash
uv run spacenav-ws serve
```
Now open: [https://127.51.68.120:8181](https://127.51.68.120:8181). When prompted, add a browser exception for the self‑signed cert.

4. **Install Tampermonkey and add the userscript**

Install [Tampermonkey](https://addons.mozilla.org/en-US/firefox/addon/tampermonkey/?utm_source=addons.mozilla.org&utm_medium=referral&utm_content=search). After installing, click this [link](https://greasyfork.org/en/scripts/533516-onshape-3d-mouse-on-linux-in-page-patch) for one‑click install of the script.

5. **Open an Onshape document and test your mouse!**

## Developing

```bash
uv run spacenav-ws serve --hot-reload
```

This starts the server with Uvicorn’s `code watching / hot reload` feature enabled. When making changes the server restarts and any websocket state is nuked, however, Onshape should immediately reconnect automatically! This makes for a very smooth and fast iteration workflow.
