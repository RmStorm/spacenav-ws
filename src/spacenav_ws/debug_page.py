from __future__ import annotations

from html import escape

ONSHAPE_USER_SCRIPT = """// ==UserScript==
// @name         Onshape 3D-Mouse on Linux (in-page patch)
// @description  Fake navigator.platform to convince Onshape to use the local nlproxy bridge.
// @match        https://cad.onshape.com/documents/*
// @run-at       document-start
// @grant        none
// @version      0.0.2
// @license      GPL-3.0-only
// ==/UserScript==

Object.defineProperty(Navigator.prototype, "platform", { get: () => "Win32" });
console.log("[Onshape patch] navigator.platform ->", navigator.platform);
"""


def render_debug_page(
    host: str,
    port: int,
    user_script_url: str,
) -> str:
    origin = f"https://{host}:{port}"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>spacenav-ws debug</title>
  </head>
  <body>
    <h1>spacenav-ws debug</h1>

    <h2>Use It</h2>
    <ol>
      <li>Open <a href="{escape(origin)}">{escape(origin)}</a> and trust the certificate.</li>
      <li>Install the Onshape userscript from <a href="{escape(user_script_url)}">{escape(user_script_url)}</a>.</li>
      <li>Open an Onshape document and keep that tab focused.</li>
      <li>Move the SpaceMouse.</li>
    </ol>

    <h2>Live events</h2>
    <p>If this updates when you move the device, the local bridge is working.</p>
    <pre id="output"></pre>

    <h2>Keep It Running</h2>
    <p>Optional: copy <code>docs/spacenav-ws.service</code> to
    <code>~/.config/systemd/user/spacenav-ws.service</code>, replace the repo path,
    then run <code>systemctl --user daemon-reload</code> and
    <code>systemctl --user enable --now spacenav-ws.service</code>.</p>

    <h2>Userscript</h2>
    <p>Onshape only talks to the local bridge when <code>navigator.platform</code> looks like Windows.</p>
    <p>Install it here: <a href="{escape(user_script_url)}">{escape(user_script_url)}</a></p>

    <script>
      const evtSource = new EventSource("/events");
      const maxLines = 30;
      const lines = [];
      const output = document.getElementById("output");

      evtSource.onmessage = function(event) {{
        lines.push(event.data);
        if (lines.length > maxLines) {{
          lines.shift();
        }}
        output.textContent = lines.join("\\n");
      }};
    </script>
  </body>
</html>
"""
