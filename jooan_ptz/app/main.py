from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

from camera import JooanAuthError, JooanCamera, redact_secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
_LOGGER = logging.getLogger("jooan_ptz")


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
CONFIG_PATH = Path("/data/options.json")
_state_lock = threading.Lock()
_camera_io_lock = threading.Lock()
_probe_start_lock = threading.Lock()
_ptz_order_lock = threading.Lock()
_snapshot_lock = threading.Lock()
_snapshot_cache: dict[tuple[str, int, str], tuple[float, bytes]] = {}
SNAPSHOT_CACHE_TTL = 90.0
CAMERA_IO_LOCK_TIMEOUT = 2.0
DISCOVERY_GAP = 0.15
RECOVERY_GRACE = 10.0
RECOVERY_VALIDATION_INTERVAL = 300.0
MAX_PTZ_CLIENTS = 64
PTZ_DIRECTIONS = {"up", "down", "left", "right", "stop"}
PTZ_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ptz_sequences: dict[str, int] = {}
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
    "heartbeat_error": None,
    "last_deep_probe": None,
    "probe_running": False,
    "probe_started_at": None,
    "last_recovery_validation": None,
    "ptz_moving": False,
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
    # All camera service traffic is serialized. The stock JA-A12 has very
    # limited HTTP/RTSP/ONVIF workers and concurrent protocol operations can
    # make otherwise valid requests time out.
    with _camera_io_lock:
        return _validate_camera_locked(deep=deep)


def _validate_camera_locked(*, deep: bool = False) -> bool:
    _LOGGER.info("Validating JOOAN camera over the local network%s", " (deep probe)" if deep else "")
    try:
        camera = get_camera()
        update_state(configured=True, last_error=None, last_check=time.time())

        # Validate authentication against the PTZ endpoint. This is the same
        # endpoint already proven by the local integration and avoids treating
        # getPlatformID as the sole source of truth for authentication.
        camera.check_auth()
        update_state(ptz_moving=False)
        time.sleep(DISCOVERY_GAP)

        try:
            platform = camera.get_platform_id()
        except Exception as exc:
            _LOGGER.warning("Could not read camera platform information: %s", redact_secrets(exc))
            platform = None
        time.sleep(DISCOVERY_GAP)

        try:
            network = camera.get_network_state()
        except Exception as exc:
            _LOGGER.warning("Could not read camera network state: %s", redact_secrets(exc))
            network = None
        time.sleep(DISCOVERY_GAP)

        try:
            lan_support = camera.get_ap_lan_p2p_support()
        except Exception as exc:
            _LOGGER.warning("Could not read LAN capability endpoint: %s", redact_secrets(exc))
            lan_support = None
        time.sleep(DISCOVERY_GAP)

        try:
            info = camera.get_device_features()
            device_info = info.as_dict()
        except Exception as exc:
            _LOGGER.warning("Could not read camera device features on port 9898: %s", redact_secrets(exc))
            device_info = None
        time.sleep(DISCOVERY_GAP)

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
                values["onvif_info"] = camera.probe_onvif()
            except Exception as exc:
                _LOGGER.warning("Could not probe ONVIF: %s", redact_secrets(exc))
                values["onvif_info"] = {"reachable": False, "error": redact_secrets(exc)}
            try:
                values["media_probe"] = camera.probe_rtsp_streams(
                    values.get("onvif_info"),
                    channel_count=channels,
                )
            except Exception as exc:
                _LOGGER.warning("Could not probe RTSP streams: %s", redact_secrets(exc))
                values["media_probe"] = {"reachable": False, "streams": [], "error": redact_secrets(exc)}

            # Derive service health only from real protocol operations. Never
            # open a socket merely to see whether a port accepts connections.
            values["services"] = {
                "http": {
                    "port": camera.http_port,
                    "reachable": True,
                    "source": "authenticated_cgi",
                },
                "features": {
                    "port": camera.features_port,
                    "reachable": device_info is not None,
                    "source": "get_deviceFeatures",
                },
                "rtsp": {
                    "port": camera.rtsp_port,
                    "reachable": bool((values.get("media_probe") or {}).get("reachable")),
                    "source": "ffprobe",
                },
                "onvif": {
                    "port": camera.onvif_port,
                    "reachable": bool((values.get("onvif_info") or {}).get("reachable")),
                    "source": "soap",
                },
            }
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
        with _state_lock:
            previous_last_seen = _state.get("last_seen")
        update_state(
            online=online,
            authenticated=False,
            initial_scan_complete=True,
            last_error=safe_error,
            last_check=now,
            last_seen=now if online else previous_last_seen,
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
        heartbeat_online = heartbeat.get("online")
        values = {
            "configured": True,
            "last_heartbeat": now,
            "heartbeat_error": heartbeat.get("error"),
        }

        if heartbeat_online is None:
            # ICMP is not available in this container. Preserve the last known
            # camera state rather than falsely marking the camera offline or
            # falling back to a connect-only TCP heartbeat.
            update_state(**values)
            with _state_lock:
                return bool(_state.get("online"))

        online = bool(heartbeat_online)
        values["online"] = online
        if online:
            values["last_seen"] = now
            values["heartbeat_error"] = None
        else:
            values["authenticated"] = False
            values["last_error"] = heartbeat.get("error") or "Camera is offline"
        update_state(**values)
        return online
    except Exception as exc:
        update_state(
            online=False,
            authenticated=False,
            last_heartbeat=time.time(),
            last_error=redact_secrets(exc),
        )
        return False


def validation_loop() -> None:
    # Full discovery is intentionally performed only once at startup.
    update_state(probe_running=True, probe_started_at=time.time())
    try:
        validate_camera(deep=True)
    finally:
        update_state(probe_running=False)
    while True:
        time.sleep(_validation_interval())
        heartbeat_camera()
        now = time.time()
        with _state_lock:
            should_recover = (
                bool(_state.get("online"))
                and not bool(_state.get("authenticated"))
                and not bool(_state.get("probe_running"))
                and (
                    _state.get("last_recovery_validation") is None
                    or now - float(_state["last_recovery_validation"]) >= RECOVERY_VALIDATION_INTERVAL
                )
            )
        if should_recover:
            update_state(last_recovery_validation=now)
            time.sleep(RECOVERY_GRACE)
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
let authenticated=false,online=false,activeDirection=null,lastData=null;
let statusTimer=null,snapshotTimer=null,mediaVisible=!('IntersectionObserver' in window);
const STATUS_REFRESH_MS=5000;
const SNAPSHOT_REFRESH_MS=15000;
const buttons=[...document.querySelectorAll('[data-dir]')];
const ptzClient=(globalThis.crypto?.randomUUID?.()||('ptz-'+Math.random().toString(36).slice(2)));
let ptzSequence=0;
const ingressBase=new URL(window.location.href);ingressBase.search='';ingressBase.hash='';if(!ingressBase.pathname.endsWith('/'))ingressBase.pathname+='/';
function api(path){return new URL(path,ingressBase).toString()}
function ptzUrl(command,sequence){const u=new URL(api('api/ptz/'+command));u.searchParams.set('client',ptzClient);u.searchParams.set('seq',String(sequence));return u.toString()}
function enableControls(enabled){buttons.forEach(b=>b.disabled=!enabled);document.getElementById('stop').disabled=!enabled}
async function send(command,keepalive=false){if(!authenticated)return;const sequence=++ptzSequence;const out=document.getElementById('command');try{const r=await fetch(ptzUrl(command,sequence),{method:'POST',keepalive});const d=await r.json();if(!d.ignored)out.textContent=r.ok?'Comando: '+command:'Erro: '+(d.error||'falha na requisição');if(!r.ok&&r.status===401){authenticated=false;enableControls(false)}}catch(e){out.textContent='Erro: '+e.message}}
function emergencyStop(){if(!activeDirection)return;activeDirection=null;void send('stop',true)}
buttons.forEach(button=>{const dir=button.dataset.dir;button.addEventListener('pointerdown',e=>{e.preventDefault();activeDirection=dir;button.setPointerCapture?.(e.pointerId);send(dir)});const stop=()=>{if(activeDirection===dir){activeDirection=null;send('stop')}};button.addEventListener('pointerup',stop);button.addEventListener('pointercancel',stop);button.addEventListener('lostpointercapture',stop)});
document.getElementById('stop').addEventListener('click',()=>{activeDirection=null;send('stop')});
window.addEventListener('blur',emergencyStop);
window.addEventListener('pagehide',emergencyStop);

let mediaSignature='',snapshotRefreshRunning=false;
function sleep(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function loadSnapshot(img){
  try{
    const response=await fetch(
      api('api/snapshot/'+encodeURIComponent(img.dataset.stream)+'?t='+Date.now()),
      {cache:'no-store'}
    );
    const state=img.parentElement?.querySelector('[data-snapshot-state]');
    if(!response.ok){if(state)state.textContent='Falha ao atualizar; mantendo o último quadro';return false}
    const stale=response.headers.get('X-JOOAN-Snapshot')==='stale';
    const blob=await response.blob();
    const objectUrl=URL.createObjectURL(blob);
    const previous=img.dataset.objectUrl;
    img.src=objectUrl;
    img.dataset.objectUrl=objectUrl;
    img.dataset.loaded='1';
    if(state)state.textContent=stale?'Snapshot em cache (temporário)':'Snapshot atualizado';
    if(previous)URL.revokeObjectURL(previous);
    return true
  }catch(_){
    return false
  }
}
async function refreshSnapshots(){
  if(document.hidden||!mediaVisible||snapshotRefreshRunning||lastData?.probe_running||activeDirection)return;
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
    button.disabled=!!lastData?.probe_running;button.textContent=previousText
  }
}
function renderMedia(data,force=false){
  const root=document.getElementById('media');
  const probe=data?.media_probe?.streams||[];
  const videoStreams=probe.filter(item=>item.available&&item.streams?.some(s=>s.codec_type==='video'));
  const byChannel=new Map(),extras=[];
  for(const item of videoStreams){
    const match=item.path.match(/^\/live\/(ch\d{2})_([01])$/);
    if(!match){extras.push(item);continue}
    const existing=byChannel.get(match[1]);
    if(!existing||match[2]==='0')byChannel.set(match[1],item)
  }
  const available=[...byChannel.values()];
  const target=Math.max(1,Number(data?.media_probe?.reported_channel_count||available.length||1));
  for(const item of extras){if(available.length>=target)break;available.push(item)}
  if(!available.length){
    mediaSignature='';
    root.querySelectorAll('img[data-object-url]').forEach(img=>URL.revokeObjectURL(img.dataset.objectUrl));
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
    const snapshotCapable=/^\/live\/ch(?:00|01)_[01]$/.test(item.path);
    if(snapshotCapable){
      const img=document.createElement('img');img.alt='Snapshot '+item.path;img.dataset.stream=stream;
      const state=document.createElement('small');state.dataset.snapshotState='1';state.textContent='Aguardando snapshot';
      box.append(title,img,state,meta)
    }else{
      const state=document.createElement('small');state.textContent='Stream ONVIF detectado; snapshot não habilitado para este path';
      box.append(title,state,meta)
    }
    root.appendChild(box)
  }
  if(!document.hidden&&mediaVisible&&!data?.probe_running)void refreshSnapshots()
}
async function deepProbe(){
  const b=document.getElementById('probe');b.disabled=true;b.textContent='Diagnosticando...';
  try{
    if(activeDirection){activeDirection=null;await send('stop')}
    const response=await fetch(api('api/probe'),{method:'POST'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    for(let i=0;i<90;i++){
      await sleep(1000);
      await refresh();
      if(!lastData?.probe_running)break
    }
    // refresh() updates media and triggers a snapshot only if the stream set changed.
  }catch(e){
    document.getElementById('command').textContent='Erro no diagnóstico: '+e.message
  }finally{
    b.disabled=!!lastData?.probe_running;
    b.textContent=lastData?.probe_running?'Diagnosticando...':'Executar diagnóstico profundo'
  }
}
document.getElementById('probe').addEventListener('click',deepProbe);
document.getElementById('snapshots').addEventListener('click',refreshSnapshots);

async function refresh(){
  try{
    const r=await fetch(api('api/status'),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();lastData=d;online=!!d.online;authenticated=!!d.authenticated;enableControls(online&&authenticated&&!d.probe_running);
    const probeButton=document.getElementById('probe');probeButton.disabled=!!d.probe_running;probeButton.textContent=d.probe_running?'Diagnosticando...':'Executar diagnóstico profundo';
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
    document.getElementById('network').textContent=JSON.stringify({
      online:d.online,
      last_seen:d.last_seen,
      last_heartbeat:d.last_heartbeat,
      heartbeat_error:d.heartbeat_error,
      network_state:d.network_state
    },null,2);
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
  if(document.hidden){emergencyStop();stopActivePolling()}
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


def _register_ptz_sequence() -> tuple[str | None, int | None, bool]:
    client_id = request.args.get("client")
    sequence_raw = request.args.get("seq")
    if client_id is None and sequence_raw is None:
        return None, None, True
    if (
        client_id is None
        or sequence_raw is None
        or not PTZ_CLIENT_RE.fullmatch(client_id)
    ):
        return None, None, False
    try:
        sequence = int(sequence_raw)
    except ValueError:
        return None, None, False
    if sequence < 0:
        return None, None, False

    with _ptz_order_lock:
        previous = _ptz_sequences.get(client_id)
        if previous is not None and sequence <= previous:
            return client_id, sequence, False
        if client_id not in _ptz_sequences and len(_ptz_sequences) >= MAX_PTZ_CLIENTS:
            _ptz_sequences.pop(next(iter(_ptz_sequences)))
        _ptz_sequences[client_id] = sequence
    return client_id, sequence, True


def _ptz_sequence_is_current(client_id: str | None, sequence: int | None) -> bool:
    if client_id is None or sequence is None:
        return True
    with _ptz_order_lock:
        return _ptz_sequences.get(client_id) == sequence


@app.post("/api/ptz/<direction>")
def ptz(direction: str):
    if direction not in PTZ_DIRECTIONS:
        return jsonify({"error": "Unsupported PTZ command"}), 400

    client_id, sequence, accepted = _register_ptz_sequence()
    if not accepted:
        if client_id is not None and sequence is not None:
            return jsonify({"ok": True, "ignored": True, "reason": "stale PTZ request"})
        return jsonify({"error": "Invalid PTZ sequence"}), 400

    with _state_lock:
        if not _state["online"] or not _state["authenticated"]:
            return jsonify({"error": "Camera is not ready"}), 503

    if not _camera_io_lock.acquire(timeout=CAMERA_IO_LOCK_TIMEOUT):
        return jsonify({"error": "Camera is busy with diagnostics or media"}), 503
    try:
        # A newer STOP/direction may have arrived while this request waited for
        # the camera lock. Never execute an older direction after a newer STOP.
        if not _ptz_sequence_is_current(client_id, sequence):
            return jsonify({"ok": True, "ignored": True, "reason": "superseded PTZ request"})
        result = get_camera().command(direction)
        update_state(ptz_moving=direction != "stop")
        return jsonify(result)
    except JooanAuthError as exc:
        safe_error = redact_secrets(exc)
        update_state(authenticated=False, last_error=safe_error)
        return jsonify({"error": safe_error}), 401
    except Exception as exc:
        safe_error = redact_secrets(exc)
        update_state(last_error=safe_error)
        return jsonify({"error": safe_error}), 502
    finally:
        _camera_io_lock.release()


@app.post("/api/test")
def test():
    with _state_lock:
        if _state.get("probe_running") or _state.get("ptz_moving"):
            return jsonify({"ok": False, "busy": True}), 409
    if not _camera_io_lock.acquire(blocking=False):
        return jsonify({"ok": False, "busy": True}), 409
    try:
        return jsonify({"ok": _validate_camera_locked(deep=False)})
    finally:
        _camera_io_lock.release()


def _manual_probe_worker() -> None:
    try:
        validate_camera(deep=True)
    finally:
        update_state(probe_running=False)


@app.post("/api/probe")
def deep_probe():
    with _state_lock:
        if _state.get("ptz_moving"):
            return jsonify({
                "ok": False,
                "error": "Stop PTZ movement before starting diagnostics",
            }), 409

    # Never keep a Gunicorn request open for a complete ONVIF + RTSP scan.
    # Start one background probe and let /api/status report progress.
    with _probe_start_lock:
        with _state_lock:
            if _state.get("probe_running"):
                return jsonify({"ok": True, "started": False, "running": True}), 202
        update_state(probe_running=True, probe_started_at=time.time())
        try:
            threading.Thread(
                target=_manual_probe_worker,
                name="camera-manual-probe",
                daemon=True,
            ).start()
        except Exception:
            update_state(probe_running=False)
            raise
    return jsonify({"ok": True, "started": True, "running": True}), 202


def _snapshot_cache_key(stream: str) -> tuple[str, int, str]:
    config = load_config()
    return (
        str(config.get("camera_ip", "")),
        int(config.get("rtsp_port", 554)),
        stream,
    )


def _capture_snapshot_with_fallback(stream: str) -> tuple[bytes, bool]:
    """Capture one frame safely and keep only a short-lived last-good image."""
    key = _snapshot_cache_key(stream)
    with _snapshot_lock:
        now = time.monotonic()
        cached_entry = _snapshot_cache.get(key)
        cached = None
        if cached_entry is not None:
            cached_at, cached_image = cached_entry
            if now - cached_at <= SNAPSHOT_CACHE_TTL:
                cached = cached_image
            else:
                _snapshot_cache.pop(key, None)

        with _state_lock:
            ptz_moving = bool(_state.get("ptz_moving"))
        if ptz_moving:
            if cached is not None:
                return cached, True
            raise RuntimeError("Snapshot paused while PTZ is moving")

        if not _camera_io_lock.acquire(timeout=CAMERA_IO_LOCK_TIMEOUT):
            if cached is not None:
                return cached, True
            raise RuntimeError("Camera is busy with diagnostics or another media operation")

        try:
            image = get_camera().snapshot(stream)
        except Exception:
            if cached is not None:
                return cached, True
            raise
        finally:
            _camera_io_lock.release()

        _snapshot_cache[key] = (time.monotonic(), image)
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
