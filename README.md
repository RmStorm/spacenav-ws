# spacenav-ws

`spacenav-ws` exposes a local SpaceMouse to browser clients over TLS WebSocket.
The main target is Onshape on Linux via `spacenavd`.

## Install

```bash
uv sync
```

Requirements:

- Linux with `spacenavd` running
- OpenSSL available if you want managed self-signed certs
- a browser with a userscript manager for Onshape

## Quick Start

```bash
uv run spacenav-ws serve --host 127.51.68.120 --port 8181
```

This lazily creates or reuses managed certs in the default state dir:
`~/.local/state/spacenav-ws/` for normal user runs, or `$XDG_STATE_HOME/spacenav-ws/`
if `XDG_STATE_HOME` is set.

Then open `https://127.51.68.120:8181` and:

1. Trust the generated cert.
2. Install the Onshape userscript from the landing page.
3. Open an Onshape document.
4. Keep that browser tab focused.
5. Move the SpaceMouse.

## Controls

- button `0`: toggle `object` / `target-camera`
- button `1`: cycle `all` -> `rotation-only` -> `translation-only` -> `all`

## Commands

Read raw SpaceMouse packets:

```bash
uv run spacenav-ws read-mouse
```

Override the raw-axis remap:

```bash
uv run spacenav-ws serve --remap XYzUWV
```

Write or reuse managed TLS material:

```bash
uv run spacenav-ws cert ensure --host 127.51.68.120
```

`remap` is six characters:

- chars `1..3`: model translation `x y z`
- chars `4..6`: model rotation `u v w`
- uppercase: positive raw axis
- lowercase: negative raw axis
- `x y z u v w` must each appear exactly once

## Certs And State

- Unprivileged runs default to the user state dir, usually `~/.local/state/spacenav-ws/`.
- Root or system-service runs default to `/var/lib/spacenav-ws/`.
- The preferred normal-user flow is to let `serve` lazily create and reuse certs in that default user state dir.
- Override either case with `--state-root` or `SPACENAV_WS_STATE_DIR`.
- Pass `--cert-file` and `--key-file` if you do not want managed self-signed certs.

## systemd

If you want a persistent user service, start from
[`docs/spacenav-ws.service`](docs/spacenav-ws.service).

Copy it to `~/.config/systemd/user/spacenav-ws.service`, replace
`/path/to/repo` with your checkout path, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now spacenav-ws.service
```

Keep `spacenavd` dead-zone/button handling sane; avoid extra gain scaling and `bnact*` remaps.

Tests:

```bash
uv run pytest -q
```

## Reference

- [`docs/navigation-model.md`](docs/navigation-model.md): navigation math
- [`docs/onshape-observations.md`](docs/onshape-observations.md): Onshape bridge contract
