import json
import os
from pathlib import Path

from flask import Flask, jsonify, Response

from camera import JooanCamera

app = Flask(__name__)
CONFIG_PATH = Path("/data/options.json")


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "camera_ip": os.environ.get("CAMERA_IP", ""),
        "camera_user": os.environ.get("CAMERA_USER", "admin"),
        "camera_password": os.environ.get("CAMERA_PASSWORD", ""),
    }


def camera():
    config = load_config()
    if not config.get("camera_ip"):
        raise ValueError("Camera IP is not configured")
    if not config.get("camera_user") or not config.get("camera_password"):
        raise ValueError("Camera username and password must be configured")
    return JooanCamera(
        config["camera_ip"],
        config.get("camera_user", "admin"),
        config.get("camera_password", ""),
    )


def validate_camera():
    """Validate configuration and credentials using a read-only camera endpoint."""
    config = load_config()
    if not config.get("camera_ip"):
        return False, "Camera IP is not configured"
    if not config.get("camera_user") or not config.get("camera_password"):
        return False, "Camera username and password must be configured"

    try:
        result = camera().test()
        if isinstance(result, dict) and result.get("result") == "error_passwd":
            return False, "Camera rejected the configured credentials"
        return True, "Camera authenticated successfully"
    except Exception as exc:
        return False, f"Camera validation failed: {exc}"


@app.get("/")
def index():
    return Response(
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JOOAN PTZ</title>
<style>
body{font-family:system-ui,sans-serif;max-width:520px;margin:30px auto;padding:20px;text-align:center}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-width:300px;margin:30px auto}
button{font-size:28px;padding:20px;border-radius:12px;border:1px solid #777;background:#eee;cursor:pointer}
button:disabled{opacity:.4;cursor:not-allowed}
.stop{font-size:18px}
#status{min-height:24px}
#message{padding:12px;border-radius:8px;background:#eee}
</style>
</head>
<body>
<h1>JOOAN PTZ</h1>
<p id="status">Validating camera...</p>
<p id="message">PTZ controls are disabled until the camera is authenticated.</p>
<div class="grid">
<div></div><button class="ptz" disabled onclick="ptz('up')">↑</button><div></div>
<button class="ptz" disabled onclick="ptz('left')">←</button><button class="ptz stop" disabled onclick="ptz('stop')">STOP</button><button class="ptz" disabled onclick="ptz('right')">→</button>
<div></div><button class="ptz" disabled onclick="ptz('down')">↓</button><div></div>
</div>
<script>
let authenticated=false;
function setControls(enabled){
  authenticated=enabled;
  document.querySelectorAll('.ptz').forEach(b=>b.disabled=!enabled);
}
async function validate(){
  setControls(false);
  try{
    const r=await fetch('/api/status',{cache:'no-store'});
    const d=await r.json();
    const status=document.getElementById('status');
    const message=document.getElementById('message');
    status.textContent=d.authenticated ? 'Camera authenticated' : 'Camera not available';
    message.textContent=d.message;
    setControls(Boolean(d.authenticated));
  }catch(e){
    document.getElementById('status').textContent='Camera not available';
    document.getElementById('message').textContent='Unable to validate the camera connection.';
    setControls(false);
  }
}
async function ptz(command){
  if(!authenticated)return;
  const s=document.getElementById('status'); s.textContent='Sending '+command+'...';
  try{
    const r=await fetch('/api/ptz/'+command,{method:'POST'});
    const text=await r.text();
    let d;
    try { d=JSON.parse(text); }
    catch { throw new Error('Camera/API returned invalid JSON: '+text.slice(0,120)); }
    s.textContent=r.ok ? 'Result: '+(d.result||'success') : 'Error: '+(d.error||'request failed');
    if(r.status===401) validate();
  }catch(e){s.textContent='Error: '+e.message;}
}
validate();
setInterval(validate,30000);
</script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.get("/api/status")
def status():
    authenticated, message = validate_camera()
    return jsonify({"authenticated": authenticated, "message": message})


@app.post("/api/ptz/<direction>")
def ptz(direction):
    authenticated, message = validate_camera()
    if not authenticated:
        return jsonify({"error": message}), 401
    try:
        result = camera().command(direction)
        if isinstance(result, dict) and result.get("result") == "error_passwd":
            return jsonify({"error": "Camera rejected the configured credentials"}), 401
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/test")
def test():
    authenticated, message = validate_camera()
    return jsonify({"ok": authenticated, "message": message}), (200 if authenticated else 401)


@app.get("/api/network")
def network():
    authenticated, message = validate_camera()
    if not authenticated:
        return jsonify({"error": message}), 401
    try:
        return jsonify(camera().get_network_state())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
