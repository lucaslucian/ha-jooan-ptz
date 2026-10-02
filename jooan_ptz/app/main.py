from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

from camera import JooanAuthError, JooanCamera

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
_LOGGER = logging.getLogger("jooan_ptz")
CONFIG_PATH = Path("/data/options.json")
_TRUSTED_INGRESS_IPS = {"172.30.32.2", "127.0.0.1", "::1"}

_state_lock = threading.Lock()
_validation_started = False
_state = {
    "configured": False,
    "authenticated": False,
    "camera_info": None,
    "network_state": None,
    "device_info": None,
    "stream_info": None,
    "last_error": None,
    "last_check": None,
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError("Home Assistant options file was not found")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get_camera() -> JooanCamera:
    config = load_config()
    if not config.get("camera_user"):
        raise ValueError("Camera username is not configured")
    if not config.get("camera_password"):
        raise ValueError("Camera password is not configured")
    return JooanCamera(
        config.get("camera_ip", ""),
        config.get("camera_user", "admin"),
        config.get("camera_password", ""),
        http_port=config.get("http_port", 80),
        features_port=config.get("features_port", 9898),
        rtsp_port=config.get("rtsp_port", 554),
        debug=bool(config.get("debug", False)),
    )


def update_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def validate_camera() -> bool:
    _LOGGER.info("Validating JOOAN camera over the local network")
    try:
        camera = get_camera()
        update_state(configured=True, last_error=None, last_check=time.time())
        platform = camera.get_platform_id()
        network = device_info = stream_info = None

        try:
            network = camera.get_network_state()
        except Exception as exc:
            _LOGGER.warning("Could not read camera network state: %s", exc)

        try:
            info = camera.get_device_features()
            device_info = info.as_dict()
        except Exception as exc:
            _LOGGER.warning("Could not read camera device features on port 9898: %s", exc)

        try:
            channels = (device_info or {}).get("channel_count", 1)
            stream_info = camera.stream_summary(channels)
        except Exception as exc:
            _LOGGER.warning("Could not confirm local RTSP settings: %s", exc)
            stream_info = {"available": False, "channel_count": (device_info or {}).get("channel_count", 1), "paths": [], "credentials_confirmed": False}

        update_state(
            authenticated=True,
            camera_info=platform,
            network_state=network,
            device_info=device_info,
            stream_info=stream_info,
            last_error=None,
            last_check=time.time(),
        )
        return True
    except Exception as exc:
        _LOGGER.warning("JOOAN camera validation failed: %s", exc)
        update_state(authenticated=False, last_error=str(exc), last_check=time.time())
        return False


def _validation_interval() -> int:
    try:
        return max(10, int(load_config().get("validation_interval", 30)))
    except Exception:
        return 30


def validation_loop() -> None:
    while True:
        time.sleep(_validation_interval())
        validate_camera()


def start_validation() -> None:
    global _validation_started
    if _validation_started:
        return
    _validation_started = True
    validate_camera()
    threading.Thread(target=validation_loop, name="camera-validation", daemon=True).start()


@app.before_request
def restrict_to_home_assistant_ingress():
    remote = request.remote_addr or ""
    if remote not in _TRUSTED_INGRESS_IPS:
        _LOGGER.warning("Rejected non-Ingress request from %s", remote)
        return jsonify({"error": "This interface is available only through Home Assistant Ingress"}), 403
    return None


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/")
def index():
    return Response("""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JOOAN Local Control</title>
<style>:root{color-scheme:light dark;font-family:system-ui,sans-serif}body{max-width:860px;margin:0 auto;padding:20px}h1{margin-bottom:4px}.sub{opacity:.72;margin-top:0}.card{border:1px solid #7776;border-radius:14px;padding:16px;margin:14px 0}.status{padding:12px;border-radius:10px;font-weight:600}.ok{background:#2e7d3230}.bad{background:#c6282830}.grid{display:grid;grid-template-columns:repeat(3,88px);gap:10px;justify-content:center;margin:18px auto}button{font-size:28px;min-height:72px;border-radius:14px;border:1px solid #7778;cursor:pointer}button:disabled{opacity:.35;cursor:not-allowed}.stop{font-size:15px;font-weight:700}pre{white-space:pre-wrap;word-break:break-word;overflow:auto}small{opacity:.72}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#7772;margin:2px}</style></head><body>
<h1>JOOAN Local Control</h1><p class="sub">Controle direto pela LAN. O add-on bloqueia destinos de Internet pública.</p><div id="status" class="status bad">Verificando câmera...</div>
<div class="card"><h2>PTZ</h2><small>Pressione e segure uma direção. Ao soltar, STOP é enviado automaticamente.</small><div class="grid"><div></div><button data-dir="up">↑</button><div></div><button data-dir="left">←</button><button id="stop" class="stop">STOP</button><button data-dir="right">→</button><div></div><button data-dir="down">↓</button><div></div></div><p id="command"></p></div>
<div class="card"><h2>Dispositivo</h2><pre id="device">Aguardando...</pre></div><div class="card"><h2>Plataforma</h2><pre id="info">Aguardando...</pre></div><div class="card"><h2>Rede</h2><pre id="network">Aguardando...</pre></div><div class="card"><h2>RTSP local</h2><div id="stream">Aguardando...</div></div>
<script>let authenticated=false,activeDirection=null;const buttons=[...document.querySelectorAll('[data-dir]')];function api(path){return new URL(path,window.location.href).toString()}function enableControls(enabled){buttons.forEach(b=>b.disabled=!enabled);document.getElementById('stop').disabled=!enabled}async function send(command){if(!authenticated)return;const out=document.getElementById('command');try{const r=await fetch(api('api/ptz/'+command),{method:'POST'});const d=await r.json();out.textContent=r.ok?'Comando: '+command:'Erro: '+(d.error||'falha na requisição');if(!r.ok&&r.status===401){authenticated=false;enableControls(false)}}catch(e){out.textContent='Erro: '+e.message}}buttons.forEach(button=>{const dir=button.dataset.dir;button.addEventListener('pointerdown',e=>{e.preventDefault();activeDirection=dir;button.setPointerCapture?.(e.pointerId);send(dir)});const stop=()=>{if(activeDirection===dir){activeDirection=null;send('stop')}};button.addEventListener('pointerup',stop);button.addEventListener('pointercancel',stop);button.addEventListener('lostpointercapture',stop)});document.getElementById('stop').addEventListener('click',()=>{activeDirection=null;send('stop')});window.addEventListener('blur',()=>{if(activeDirection){activeDirection=null;send('stop')}});async function refresh(){try{const r=await fetch(api('api/status'),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json();authenticated=!!d.authenticated;enableControls(authenticated);const status=document.getElementById('status');status.className='status '+(authenticated?'ok':'bad');status.textContent=authenticated?'Câmera autenticada e acessível pela LAN':'Câmera indisponível ou credenciais inválidas';document.getElementById('device').textContent=d.device_info?JSON.stringify(d.device_info,null,2):'Informações não disponíveis';document.getElementById('info').textContent=d.camera_info?JSON.stringify(d.camera_info,null,2):'Informações não disponíveis';document.getElementById('network').textContent=d.network_state?JSON.stringify(d.network_state,null,2):'Informações não disponíveis';const s=d.stream_info||{};document.getElementById('stream').innerHTML=s.available?'<span class="pill">'+(s.channel_count||1)+' canal(is)</span> <span class="pill">RTSP confirmado</span><pre>'+JSON.stringify(s.paths||[],null,2)+'</pre>':'RTSP ainda não confirmado neste dispositivo.';document.getElementById('command').textContent=d.last_error?'Último erro: '+d.last_error:''}catch(e){authenticated=false;enableControls(false);document.getElementById('status').textContent='Add-on/API indisponível';document.getElementById('command').textContent='Erro: '+e.message}}enableControls(false);refresh();setInterval(refresh,5000)</script></body></html>""", mimetype="text/html")


@app.get("/api/status")
def status():
    with _state_lock:
        return jsonify(dict(_state))


@app.post("/api/ptz/<direction>")
def ptz(direction: str):
    with _state_lock:
        if not _state["authenticated"]:
            return jsonify({"error": "Camera is not authenticated"}), 503
    try:
        return jsonify(get_camera().command(direction))
    except JooanAuthError as exc:
        update_state(authenticated=False, last_error=str(exc))
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        update_state(last_error=str(exc))
        return jsonify({"error": str(exc)}), 502


@app.post("/api/test")
def test():
    return jsonify({"ok": validate_camera()})


if __name__ == "__main__":
    start_validation()
    app.run(host="0.0.0.0", port=8099)
