import json
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, Response

from camera import JooanCamera

app = Flask(__name__)
_LOGGER = logging.getLogger("jooan_ptz")
CONFIG_PATH = Path("/data/options.json")

_state_lock = threading.Lock()
_state = {
    "configured": False,
    "authenticated": False,
    "camera_info": None,
    "network_state": None,
    "last_error": None,
    "last_check": None,
}


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "camera_ip": os.environ.get("CAMERA_IP", ""),
        "camera_user": os.environ.get("CAMERA_USER", "admin"),
        "camera_password": os.environ.get("CAMERA_PASSWORD", ""),
        "debug": True,
    }


def get_camera():
    config = load_config()
    if not config.get("camera_ip"):
        raise ValueError("Camera IP is not configured")
    if not config.get("camera_user"):
        raise ValueError("Camera username is not configured")
    if not config.get("camera_password"):
        raise ValueError("Camera password is not configured")
    return JooanCamera(
        config["camera_ip"],
        config.get("camera_user", "admin"),
        config.get("camera_password", ""),
        debug=bool(config.get("debug", True)),
    )


def update_state(**values):
    with _state_lock:
        _state.update(values)


def validate_camera():
    try:
        camera = get_camera()
        update_state(configured=True, last_error=None, last_check=time.time())
        _LOGGER.info("Checking JOOAN camera authentication with getPlatformID")
        platform = camera.get_platform_id()
        if isinstance(platform, dict) and platform.get("result") == "error_passwd":
            raise PermissionError("Camera rejected the credentials")
        _LOGGER.info("JOOAN getPlatformID returned a response")

        network = None
        try:
            network = camera.get_network_state()
        except Exception as exc:
            _LOGGER.warning("Could not read camera network state: %s", exc)

        update_state(
            authenticated=True,
            camera_info=platform,
            network_state=network,
            last_error=None,
            last_check=time.time(),
        )
        return True
    except Exception as exc:
        _LOGGER.warning("JOOAN camera validation failed: %s", exc)
        update_state(authenticated=False, last_error=str(exc), last_check=time.time())
        return False


def validation_loop():
    # The initial check is performed synchronously by start_validation().
    # Subsequent checks keep the connection state fresh without blocking
    # application startup.
    while True:
        time.sleep(30)
        validate_camera()


def start_validation():
    # Validate immediately during startup so the configured credentials are
    # always checked before the web UI becomes available. This also guarantees
    # that the request/response appears in the debug log on every start.
    validate_camera()
    threading.Thread(target=validation_loop, name="camera-validation", daemon=True).start()


@app.get("/")
def index():
    return Response(
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JOOAN PTZ</title><style>body{font-family:system-ui,sans-serif;max-width:700px;margin:30px auto;padding:20px;text-align:center}.status{padding:12px;border-radius:10px;margin:15px 0}.ok{background:#dff5df}.bad{background:#f8dddd}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-width:300px;margin:30px auto}button{font-size:28px;padding:20px;border-radius:12px;border:1px solid #777;background:#eee;cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.stop{font-size:18px}pre{text-align:left;white-space:pre-wrap;word-break:break-word;background:#eee;padding:12px;border-radius:8px}</style></head><body><h1>JOOAN PTZ</h1><div id="status" class="status bad">Checking camera...</div><div class="grid"><div></div><button data-ptz="up" onclick="ptz('up')">↑</button><div></div><button data-ptz="left" onclick="ptz('left')">←</button><button data-ptz="stop" class="stop" onclick="ptz('stop')">STOP</button><button data-ptz="right" onclick="ptz('right')">→</button><div></div><button data-ptz="down" onclick="ptz('down')">↓</button><div></div></div><h2>Camera information</h2><pre id="info">Waiting for camera...</pre><h2>Network information</h2><pre id="network">Waiting for camera...</pre><p id="command"></p><script>let authenticated=false;function setButtons(e){document.querySelectorAll('[data-ptz]').forEach(b=>b.disabled=!e)}async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'}),d=await r.json();authenticated=!!d.authenticated;setButtons(authenticated);const s=document.getElementById('status');s.className='status '+(authenticated?'ok':'bad');s.textContent=authenticated?'Camera authenticated':'Camera unavailable or credentials invalid';document.getElementById('info').textContent=d.camera_info?JSON.stringify(d.camera_info,null,2):'No information available';document.getElementById('network').textContent=d.network_state?JSON.stringify(d.network_state,null,2):'No network information available';document.getElementById('command').textContent=d.last_error?'Error: '+d.last_error:''}catch(e){setButtons(false);document.getElementById('status').textContent='Add-on/API unavailable'}}async function ptz(command){if(!authenticated)return;const s=document.getElementById('command');s.textContent='Sending '+command+'...';try{const r=await fetch('/api/ptz/'+command,{method:'POST'}),text=await r.text();let d;try{d=JSON.parse(text)}catch{throw new Error('Camera/API returned invalid JSON: '+text.slice(0,120))}s.textContent=r.ok?'Result: '+(d.result||'success'):'Error: '+(d.error||'request failed');if(r.status===401){authenticated=false;setButtons(false)}}catch(e){s.textContent='Error: '+e.message}}setButtons(false);refresh();setInterval(refresh,5000)</script></body></html>""",
        mimetype="text/html",
    )


@app.get("/api/status")
def status():
    with _state_lock:
        return jsonify(_state)


@app.post("/api/ptz/<direction>")
def ptz(direction):
    with _state_lock:
        if not _state["authenticated"]:
            return jsonify({"error": "Camera is not authenticated"}), 503
    try:
        result = get_camera().command(direction)
        if isinstance(result, dict) and result.get("result") == "error_passwd":
            update_state(authenticated=False, last_error="Camera rejected the credentials")
            return jsonify(result), 401
        return jsonify(result)
    except Exception as exc:
        update_state(authenticated=False, last_error=str(exc))
        return jsonify({"error": str(exc)}), 502


@app.get("/api/test")
def test():
    return jsonify({"ok": validate_camera()})


@app.get("/api/network")
def network():
    with _state_lock:
        if not _state["authenticated"]:
            return jsonify({"error": "Camera is not authenticated"}), 503
        return jsonify(_state["network_state"] or {})


start_validation()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
