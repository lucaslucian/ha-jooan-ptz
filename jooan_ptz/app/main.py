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
    return JooanCamera(
        config["camera_ip"],
        config.get("camera_user", "admin"),
        config.get("camera_password", ""),
    )


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
.stop{font-size:18px}
#status{min-height:24px}
</style>
</head>
<body>
<h1>JOOAN PTZ</h1>
<p id="status">Ready</p>
<div class="grid">
<div></div><button onclick="ptz('up')">↑</button><div></div>
<button onclick="ptz('left')">←</button><button class="stop" onclick="ptz('stop')">STOP</button><button onclick="ptz('right')">→</button>
<div></div><button onclick="ptz('down')">↓</button><div></div>
</div>
<script>
async function ptz(command){
  const s=document.getElementById('status'); s.textContent='Sending '+command+'...';
  try{
    const r=await fetch('/api/ptz/'+command,{method:'POST'});
    const text=await r.text();
    let d;
    try { d=JSON.parse(text); }
    catch { throw new Error('Camera/API returned invalid JSON: '+text.slice(0,120)); }
    s.textContent=r.ok ? 'Result: '+(d.result||'success') : 'Error: '+(d.error||'request failed');
  }catch(e){s.textContent='Error: '+e.message;}
}
</script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.post("/api/ptz/<direction>")
def ptz(direction):
    try:
        result = camera().command(direction)
        if isinstance(result, dict) and result.get("result") == "error_passwd":
            return jsonify(result), 401
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/api/test")
def test():
    try:
        result = camera().test()
        return jsonify({"ok": True, "platform": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.get("/api/network")
def network():
    try:
        return jsonify(camera().get_network_state())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
