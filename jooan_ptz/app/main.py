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
_snapshot_lock = threading.Lock()
_snapshot_cache: dict[tuple[str, int, str], bytes] = {}
_validation_started = False
_state = {
    "configured": False,
    "online": False,
    "authenticated": False,
    "initial_scan_complete": False,
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
    "last_seen": None,
    "last_heartbeat": None,
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

        now = time.time()
        values = {
            "online": True,
            "authenticated": True,
            "camera_info": platform,
            "network_state": network,
            "lan_support": lan_support,
            "device_info": device_info,
            "stream_info": stream_info,
            "last_error": None,
            "last_check": now,
            "last_seen": now,
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

        values["initial_scan_complete"] = True
        update_state(**values)
        return True
    except Exception as exc:
        safe_error = redact_secrets(exc)
        _LOGGER.warning("JOOAN camera validation failed: %s", safe_error)
        online = False
        try:
            heartbeat = get_camera().heartbeat()
            online = bool(heartbeat.get("online"))
        except Exception:
            pass
        now = time.time()
        update_state(
            online=online,
            authenticated=False,
            initial_scan_complete=True,
            last_error=safe_error,
            last_check=now,
            last_seen=now if online else _state.get("last_seen"),
        )
        return False


def _validation_interval() -> int:
    try:
        return max(10, int(load_config().get("validation_interval", 30)))
    except Exception:
        return 30


def heartbeat_camera() -> bool:
    """Update only liveness between full/manual diagnostics."""
    try:
        heartbeat = get_camera().heartbeat()
        now = time.time()
        online = bool(heartbeat.get("online"))
        values = {
            "configured": True,
            "online": online,
            "last_heartbeat": now,
        }
        if online:
            values["last_seen"] = now
            if _state.get("authenticated"):
                values["last_error"] = None
        else:
            values["last_error"] = heartbeat.get("error") or "Camera is offline"
        update_state(**values)
        return online
    except Exception as exc:
        update_state(
            online=False,
            last_heartbeat=time.time(),
            last_error=redact_secrets(exc),
        )
        return False


def validation_loop() -> None:
    # Full discovery is intentionally performed only once at startup.
    validate_camera(deep=True)
    while True:
        time.sleep(_validation_interval())
        heartbeat_camera()


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
let authenticated=false,online=false,activeDirection=null,lastData=null;
let statusTimer=null,snapshotTimer=null,mediaVisible=true;
const STATUS_REFRESH_MS=5000;
const SNAPSHOT_REFRESH_MS=15000;
const buttons=[...document.querySelectorAll('[data-dir]')];
function api(path){return new URL(path,window.location.href).toString()}
function enableControls(enabled){buttons.forEach(b=>b.disabled=!enabled);document.getElementById('stop').disabled=!enabled}
async function send(command){if(!authenticated)return;const out=document.getElementById('command');try{const r=await fetch(api('api/ptz/'+command),{method:'POST'});const d=await r.json();out.textContent=r.ok?'Comando: '+command:'Erro: '+(d.error||'falha na requisição');if(!r.ok&&r.status===401){authenticated=false;enableControls(false)}}catch(e){out.textContent='Erro: '+e.message}}
buttons.forEach(button=>{const dir=button.dataset.dir;button.addEventListener('pointerdown',e=>{e.preventDefault();activeDirection=dir;button.setPointerCapture?.(e.pointerId);send(dir)});const stop=()=>{if(activeDirection===dir){activeDirection=null;send('stop')}};button.addEventListener('pointerup',stop);button.addEventListener('pointercancel',stop);button.addEventListener('lostpointercapture',stop)});
document.getElementById('stop').addEventListener('click',()=>{activeDirection=null;send('stop')});
window.addEventListener('blur',()=>{if(activeDirection){activeDirection=null;send('stop')}});

let mediaSignature='',snapshotRefreshRunning=false;
function sleep(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function loadSnapshot(img){
  try{
    const response=await fetch(
      api('api/snapshot/'+encodeURIComponent(img.dataset.stream)+'?t='+Date.now()),
      {cache:'no-store'}
    );
    if(!response.ok)return false;
    const blob=await response.blob();
    const objectUrl=URL.createObjectURL(blob);
    const previous=img.dataset.objectUrl;
    img.src=objectUrl;
    img.dataset.objectUrl=objectUrl;
    img.dataset.loaded='1';
    if(previous)URL.revokeObjectURL(previous);
    return true
  }catch(_){
    return false
  }
}
async function refreshSnapshots(){
  if(document.hidden||!mediaVisible||snapshotRefreshRunning)return;
  snapshotRefreshRunning=true;
  const button=document.getElementById('snapshots');
  const previousText=button.textContent;
  button.disabled=true;button.textContent='Atualizando...';
  try{
    const images=[...document.querySelectorAll('img[data-stream]')];
    for(let i=0;i<images.length;i++){
      await loadSnapshot(images[i]);
      if(i+1<images.length)await sleep(300)
    }
  }finally{
    snapshotRefreshRunning=false;
    button.disabled=false;button.textContent=previousText
  }
}
function renderMedia(data,force=false){
  const root=document.getElementById('media');
  const probe=data?.media_probe?.streams||[];
  const videoStreams=probe.filter(item=>item.available&&item.streams?.some(s=>s.codec_type==='video'));
  const mainStreams=videoStreams.filter(item=>/_0$/.test(item.path));
  const available=mainStreams.length?mainStreams:videoStreams;
  if(!available.length){
    mediaSignature='';
    root.textContent='Nenhum stream RTSP confirmado ainda. Execute o diagnóstico profundo.';
    return
  }
  const signature=JSON.stringify(available.map(item=>({path:item.path,streams:item.streams})));
  if(!force&&signature===mediaSignature)return;
  mediaSignature=signature;
  root.querySelectorAll('img[data-object-url]').forEach(img=>URL.revokeObjectURL(img.dataset.objectUrl));
  root.innerHTML='';
  for(const item of available){
    const stream=item.path.split('/').pop();
    const box=document.createElement('div');box.className='media-item';
    const title=document.createElement('strong');title.textContent=item.path;
    const meta=document.createElement('pre');meta.textContent=JSON.stringify(item.streams,null,2);
    const img=document.createElement('img');img.alt='Snapshot '+item.path;img.dataset.stream=stream;
    box.append(title,img,meta);root.appendChild(box)
  }
  if(!document.hidden&&mediaVisible)void refreshSnapshots()
}
async function deepProbe(){
  const b=document.getElementById('probe');b.disabled=true;b.textContent='Diagnosticando...';
  try{
    await fetch(api('api/probe'),{method:'POST'});
    await refresh();
    await refreshSnapshots()
  }finally{b.disabled=false;b.textContent='Executar diagnóstico profundo'}
}
document.getElementById('probe').addEventListener('click',deepProbe);
document.getElementById('snapshots').addEventListener('click',refreshSnapshots);

async function refresh(){
  try{
    const r=await fetch(api('api/status'),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();lastData=d;online=!!d.online;authenticated=!!d.authenticated;enableControls(online&&authenticated);
    const status=document.getElementById('status');status.className='status '+(online&&authenticated?'ok':'bad');
    if(!online)status.textContent='Câmera offline';
    else if(!authenticated)status.textContent='Câmera online, mas autenticação não validada';
    else status.textContent='Câmera autenticada e acessível pela LAN';
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
function stopActivePolling(){
  if(statusTimer){clearInterval(statusTimer);statusTimer=null}
  if(snapshotTimer){clearInterval(snapshotTimer);snapshotTimer=null}
}
function startActivePolling(){
  stopActivePolling();
  if(document.hidden)return;
  void refresh();
  statusTimer=setInterval(()=>{if(!document.hidden)void refresh()},STATUS_REFRESH_MS);
  snapshotTimer=setInterval(()=>{
    if(!document.hidden&&mediaVisible)void refreshSnapshots()
  },SNAPSHOT_REFRESH_MS)
}
const mediaRoot=document.getElementById('media');
if('IntersectionObserver' in window){
  const observer=new IntersectionObserver(entries=>{
    mediaVisible=entries.some(entry=>entry.isIntersecting);
    if(mediaVisible&&!document.hidden)void refreshSnapshots()
  },{threshold:0.05});
  observer.observe(mediaRoot)
}
document.addEventListener('visibilitychange',()=>{
  if(document.hidden)stopActivePolling();
  else startActivePolling()
});
window.addEventListener('pagehide',stopActivePolling);
window.addEventListener('pageshow',()=>{if(!document.hidden)startActivePolling()});
enableControls(false);startActivePolling()
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


def _snapshot_cache_key(stream: str) -> tuple[str, int, str]:
    config = load_config()
    return (
        str(config.get("camera_ip", "")),
        int(config.get("rtsp_port", 554)),
        stream,
    )


def _capture_snapshot_with_fallback(stream: str) -> tuple[bytes, bool]:
    """Capture one RTSP frame at a time and preserve the last good frame.

    The stock JA-A12 becomes unreliable when several FFmpeg/RTSP sessions are
    opened concurrently. A single process-wide lock keeps snapshot captures
    serialized. If a transient RTSP capture fails after a successful frame was
    already obtained, return that last good frame instead of replacing the UI
    image with an HTTP 502 response.
    """
    key = _snapshot_cache_key(stream)
    with _snapshot_lock:
        cached = _snapshot_cache.get(key)
        try:
            image = get_camera().snapshot(stream)
        except Exception:
            if cached is not None:
                return cached, True
            raise
        _snapshot_cache[key] = image
        return image, False


@app.get("/api/snapshot/<stream>")
def snapshot(stream: str):
    try:
        image, stale = _capture_snapshot_with_fallback(stream)
        return Response(
            image,
            mimetype="image/jpeg",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-JOOAN-Snapshot": "stale" if stale else "fresh",
            },
        )
    except Exception as exc:
        _LOGGER.warning("Snapshot failed for %s: %s", stream, redact_secrets(exc))
        return jsonify({"error": "Snapshot unavailable for this local stream"}), 502


if __name__ == "__main__":
    start_validation()
    app.run(host="0.0.0.0", port=8099)
