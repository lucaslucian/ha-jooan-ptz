from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify

from camera import JooanAuthError, JooanCamera, redact_secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
_LOGGER = logging.getLogger("jooan_ptz")
CONFIG_PATH = Path("/data/options.json")
_state_lock = threading.Lock()
_validation_started = False
_state = {
    "configured": False,
    "authenticated": False,
    "camera_info": None,
    "network_state": None,
    "lan_support": None,
    "device_info": None,
    "stream_info": None,
    "services": None,
    "onvif_info": None,
    "media_probe": None,
    "last_error": None,
    "last_check": None,
    "last_deep_probe": None,
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
        onvif_port=config.get("onvif_port", 8899),
        debug=bool(config.get("debug", False)),
    )


def update_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def validate_camera(*, deep: bool = False) -> bool:
    _LOGGER.info("Validating JOOAN camera over the local network%s", " (deep probe)" if deep else "")
    try:
        camera = get_camera()
        update_state(configured=True, last_error=None, last_check=time.time())

        # Validate authentication against the PTZ endpoint. This is the same
        # endpoint already proven by the local integration and avoids treating
        # getPlatformID as the sole source of truth for authentication.
        camera.check_auth()

        try:
            platform = camera.get_platform_id()
        except Exception as exc:
            _LOGGER.warning("Could not read camera platform information: %s", redact_secrets(exc))
            platform = None

        try:
            network = camera.get_network_state()
        except Exception as exc:
            _LOGGER.warning("Could not read camera network state: %s", redact_secrets(exc))
            network = None

        try:
            lan_support = camera.get_ap_lan_p2p_support()
        except Exception as exc:
            _LOGGER.warning("Could not read LAN capability endpoint: %s", redact_secrets(exc))
            lan_support = None

        try:
            info = camera.get_device_features()
            device_info = info.as_dict()
        except Exception as exc:
            _LOGGER.warning("Could not read camera device features on port 9898: %s", redact_secrets(exc))
            device_info = None

        try:
            channels = (device_info or {}).get("channel_count", 1)
            stream_info = camera.stream_summary(channels)
        except Exception as exc:
            _LOGGER.warning("Could not confirm local RTSP settings: %s", redact_secrets(exc))
            stream_info = {
                "credentials_confirmed": False,
                "candidate_paths": [],
                "reported_channel_count": (device_info or {}).get("channel_count", 1),
            }

        values = {
            "authenticated": True,
            "camera_info": platform,
            "network_state": network,
            "lan_support": lan_support,
            "device_info": device_info,
            "stream_info": stream_info,
            "last_error": None,
            "last_check": time.time(),
        }

        if deep:
            try:
                values["services"] = camera.probe_services()
            except Exception as exc:
                _LOGGER.warning("Could not probe local service ports: %s", redact_secrets(exc))
            try:
                values["onvif_info"] = camera.probe_onvif()
            except Exception as exc:
                _LOGGER.warning("Could not probe ONVIF: %s", redact_secrets(exc))
                values["onvif_info"] = {"reachable": False, "error": redact_secrets(exc)}
            try:
                values["media_probe"] = camera.probe_rtsp_streams(values.get("onvif_info"))
            except Exception as exc:
                _LOGGER.warning("Could not probe RTSP streams: %s", redact_secrets(exc))
                values["media_probe"] = {"reachable": False, "streams": [], "error": redact_secrets(exc)}
            values["last_deep_probe"] = time.time()

        update_state(**values)
        return True
    except Exception as exc:
        safe_error = redact_secrets(exc)
        _LOGGER.warning("JOOAN camera validation failed: %s", safe_error)
        update_state(authenticated=False, last_error=safe_error, last_check=time.time())
        return False


def _validation_interval() -> int:
    try:
        return max(10, int(load_config().get("validation_interval", 30)))
    except Exception:
        return 30


def validation_loop() -> None:
    validate_camera(deep=True)
    while True:
        time.sleep(_validation_interval())
        validate_camera(deep=False)


def start_validation() -> None:
    global _validation_started
    if _validation_started:
        return
    _validation_started = True
    threading.Thread(target=validation_loop, name="camera-validation", daemon=True).start()


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/")
def index():
    return Response("""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JOOAN Local Control</title>
<style>:root{color-scheme:light dark;font-family:system-ui,sans-serif}body{max-width:1080px;margin:0 auto;padding:20px}h1{margin-bottom:4px}.sub{opacity:.72;margin-top:0}.card{border:1px solid #7776;border-radius:14px;padding:16px;margin:14px 0}.status{padding:12px;border-radius:10px;font-weight:600}.ok{background:#2e7d3230}.bad{background:#c6282830}.grid{display:grid;grid-template-columns:repeat(3,88px);gap:10px;justify-content:center;margin:18px auto}button{min-height:44px;border-radius:10px;border:1px solid #7778;cursor:pointer;padding:8px 14px}.ptz button{font-size:28px;min-height:72px}.ptz button:disabled{opacity:.35;cursor:not-allowed}.stop{font-size:15px!important;font-weight:700}.media{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.media-item{border:1px solid #7775;border-radius:10px;padding:10px}.media-item img{width:100%;aspect-ratio:16/9;object-fit:contain;background:#000;border-radius:8px}pre{white-space:pre-wrap;word-break:break-word;overflow:auto;max-height:420px}small{opacity:.72}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#7772;margin:2px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}</style></head><body>
<h1>JOOAN Local Control</h1><p class="sub">Controle e diagnóstico pela LAN. Destinos públicos de Internet são recusados pelo backend.</p><div id="status" class="status bad">Verificando câmera...</div>

<div class="card ptz"><h2>PTZ</h2><small>Pressione e segure uma direção. Ao soltar, STOP é enviado automaticamente.</small><div class="grid"><div></div><button data-dir="up">↑</button><div></div><button data-dir="left">←</button><button id="stop" class="stop">STOP</button><button data-dir="right">→</button><div></div><button data-dir="down">↓</button><div></div></div><p id="command"></p></div>

<div class="card"><h2>Mídia local</h2><div class="actions"><button id="probe">Executar diagnóstico profundo</button><button id="snapshots">Atualizar snapshots</button></div><div id="media" class="media">Aguardando diagnóstico...</div></div>

<div class="card"><h2>Capacidades e estado local</h2><pre id="capabilities">Aguardando...</pre></div>
<div class="card"><h2>Serviços locais</h2><pre id="services">Aguardando...</pre></div>
<div class="card"><h2>ONVIF</h2><pre id="onvif">Aguardando...</pre></div>
<div class="card"><h2>Dispositivo</h2><pre id="device">Aguardando...</pre></div>
<div class="card"><h2>LAN / RTSP</h2><pre id="lan">Aguardando...</pre></div>
<div class="card"><h2>Plataforma</h2><pre id="info">Aguardando...</pre></div>
<div class="card"><h2>Rede</h2><pre id="network">Aguardando...</pre></div>

<script>
let authenticated=false,activeDirection=null,lastData=null;
const buttons=[...document.querySelectorAll('[data-dir]')];
function api(path){return new URL(path,window.location.href).toString()}
function enableControls(enabled){buttons.forEach(b=>b.disabled=!enabled);document.getElementById('stop').disabled=!enabled}
async function send(command){if(!authenticated)return;const out=document.getElementById('command');try{const r=await fetch(api('api/ptz/'+command),{method:'POST'});const d=await r.json();out.textContent=r.ok?'Comando: '+command:'Erro: '+(d.error||'falha na requisição');if(!r.ok&&r.status===401){authenticated=false;enableControls(false)}}catch(e){out.textContent='Erro: '+e.message}}
buttons.forEach(button=>{const dir=button.dataset.dir;button.addEventListener('pointerdown',e=>{e.preventDefault();activeDirection=dir;button.setPointerCapture?.(e.pointerId);send(dir)});const stop=()=>{if(activeDirection===dir){activeDirection=null;send('stop')}};button.addEventListener('pointerup',stop);button.addEventListener('pointercancel',stop);button.addEventListener('lostpointercapture',stop)});
document.getElementById('stop').addEventListener('click',()=>{activeDirection=null;send('stop')});
window.addEventListener('blur',()=>{if(activeDirection){activeDirection=null;send('stop')}});

function renderMedia(data,force=false){
  const root=document.getElementById('media');
  const probe=data?.media_probe?.streams||[];
  const available=probe.filter(item=>item.available&&item.streams?.some(s=>s.codec_type==='video'));
  if(!available.length){root.textContent='Nenhum stream RTSP confirmado ainda. Execute o diagnóstico profundo.';return}
  root.innerHTML='';
  for(const item of available){
    const stream=item.path.split('/').pop();
    const box=document.createElement('div');box.className='media-item';
    const title=document.createElement('strong');title.textContent=item.path;
    const meta=document.createElement('pre');meta.textContent=JSON.stringify(item.streams,null,2);
    const img=document.createElement('img');img.alt='Snapshot '+item.path;img.dataset.stream=stream;
    img.src=api('api/snapshot/'+stream+'?t='+Date.now());
    box.append(title,img,meta);root.appendChild(box)
  }
}
function refreshSnapshots(){
  document.querySelectorAll('img[data-stream]').forEach(img=>{img.src=api('api/snapshot/'+img.dataset.stream+'?t='+Date.now())})
}
async function deepProbe(){
  const b=document.getElementById('probe');b.disabled=true;b.textContent='Diagnosticando...';
  try{await fetch(api('api/probe'),{method:'POST'});await refresh()}finally{b.disabled=false;b.textContent='Executar diagnóstico profundo'}
}
document.getElementById('probe').addEventListener('click',deepProbe);
document.getElementById('snapshots').addEventListener('click',refreshSnapshots);

async function refresh(){
  try{
    const r=await fetch(api('api/status'),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();lastData=d;authenticated=!!d.authenticated;enableControls(authenticated);
    const status=document.getElementById('status');status.className='status '+(authenticated?'ok':'bad');
    status.textContent=authenticated?'Câmera autenticada e acessível pela LAN':'Câmera indisponível ou credenciais inválidas';
    document.getElementById('device').textContent=d.device_info?JSON.stringify(d.device_info,null,2):'Informações não disponíveis';
    document.getElementById('capabilities').textContent=d.device_info?JSON.stringify({capabilities:d.device_info.capabilities,local_state:d.device_info.local_state},null,2):'Informações não disponíveis';
    document.getElementById('services').textContent=d.services?JSON.stringify(d.services,null,2):'Execute o diagnóstico profundo';
    document.getElementById('onvif').textContent=d.onvif_info?JSON.stringify(d.onvif_info,null,2):'Execute o diagnóstico profundo';
    document.getElementById('lan').textContent=JSON.stringify({lan_support:d.lan_support,stream_info:d.stream_info,media_probe:d.media_probe},null,2);
    document.getElementById('info').textContent=d.camera_info?JSON.stringify(d.camera_info,null,2):'Informações não disponíveis';
    document.getElementById('network').textContent=d.network_state?JSON.stringify(d.network_state,null,2):'Informações não disponíveis';
    document.getElementById('command').textContent=d.last_error?'Último erro: '+d.last_error:'';
    renderMedia(d);
  }catch(e){
    authenticated=false;enableControls(false);document.getElementById('status').textContent='App/API indisponível';document.getElementById('command').textContent='Erro: '+e.message
  }
}
enableControls(false);refresh();setInterval(refresh,5000)
</script></body></html>""", mimetype="text/html")


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
    return jsonify({"ok": validate_camera(deep=False)})


@app.post("/api/probe")
def deep_probe():
    return jsonify({"ok": validate_camera(deep=True)})


@app.get("/api/snapshot/<stream>")
def snapshot(stream: str):
    try:
        image = get_camera().snapshot(stream)
        return Response(
            image,
            mimetype="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    except Exception as exc:
        _LOGGER.warning("Snapshot failed for %s: %s", stream, redact_secrets(exc))
        return jsonify({"error": "Snapshot unavailable for this local stream"}), 502


if __name__ == "__main__":
    start_validation()
    app.run(host="0.0.0.0", port=8099)
