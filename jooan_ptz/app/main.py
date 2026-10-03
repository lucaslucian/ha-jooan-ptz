from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

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
_preview_state_lock = threading.Lock()
_preview_start_lock = threading.Lock()
_preview_probe_start_lock = threading.Lock()
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
_preview_process = None
_preview_generation = 0
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
    "preview_active": False,
    "preview_channel": None,
    "preview_stream": None,
    "preview_source": None,
    "preview_probe": None,
    "preview_probe_running": False,
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
                and not bool(_state.get("preview_active"))
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
    return render_template("index.html")


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
        if (
            _state.get("probe_running")
            or _state.get("ptz_moving")
            or _state.get("preview_active")
            or _state.get("preview_probe_running")
        ):
            return jsonify({"ok": False, "busy": True}), 409
    if not _camera_io_lock.acquire(blocking=False):
        return jsonify({"ok": False, "busy": True}), 409
    try:
        return jsonify({"ok": _validate_camera_locked(deep=False)})
    finally:
        _camera_io_lock.release()


def _video_stream_available(item: dict | None) -> bool:
    return bool(
        item
        and item.get("available")
        and any(
            isinstance(stream, dict) and stream.get("codec_type") == "video"
            for stream in item.get("streams", [])
        )
    )


def _select_preview_stream(channel: int) -> tuple[str, str]:
    if channel not in (0, 1):
        raise ValueError("Unsupported preview channel")

    channel_id = f"ch{channel:02d}"
    sub_path = f"/live/{channel_id}_1"
    main_path = f"/live/{channel_id}_0"

    with _state_lock:
        preview_probe = dict(_state.get("preview_probe") or {})
        onvif_info = dict(_state.get("onvif_info") or {})
        media_probe = dict(_state.get("media_probe") or {})

    for item in preview_probe.get("streams", []):
        if item.get("path") == sub_path and _video_stream_available(item):
            return sub_path, "validated_substream"

    for profile in onvif_info.get("profiles", []):
        if (profile.get("stream") or {}).get("path") == sub_path:
            return sub_path, "onvif_substream"

    for item in media_probe.get("streams", []):
        if item.get("path") == main_path and _video_stream_available(item):
            return main_path, "confirmed_main"

    # The main paths are already allowlisted and are the conservative fallback.
    return main_path, "known_main_candidate"


def _terminate_process(process) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except Exception:
                process.kill()
                process.wait(timeout=1.0)
    except Exception:
        pass


def _stop_live_preview() -> bool:
    global _preview_process, _preview_generation
    with _preview_state_lock:
        process = _preview_process
        had_preview = process is not None
        _preview_process = None
        _preview_generation += 1
    _terminate_process(process)
    update_state(
        preview_active=False,
        preview_channel=None,
        preview_stream=None,
        preview_source=None,
    )
    return had_preview


def _preview_probe_worker() -> None:
    try:
        with _camera_io_lock:
            with _state_lock:
                channel_count = int(
                    (_state.get("device_info") or {}).get("channel_count", 1)
                )
            result = get_camera().probe_preview_substreams(channel_count)
            update_state(preview_probe=result)
    except Exception as exc:
        update_state(
            preview_probe={
                "probe_mode": "substreams-only",
                "reachable": False,
                "streams": [],
                "error": redact_secrets(exc),
            }
        )
    finally:
        update_state(preview_probe_running=False)


@app.post("/api/preview/validate")
def validate_preview_substreams():
    with _state_lock:
        if (
            _state.get("probe_running")
            or _state.get("preview_active")
            or _state.get("ptz_moving")
        ):
            return jsonify({
                "ok": False,
                "error": "Camera is busy with preview, PTZ or diagnostics",
            }), 409

    with _preview_probe_start_lock:
        with _state_lock:
            if _state.get("preview_probe_running"):
                return jsonify({"ok": True, "started": False, "running": True}), 202
        update_state(preview_probe_running=True)
        threading.Thread(
            target=_preview_probe_worker,
            name="camera-preview-probe",
            daemon=True,
        ).start()
    return jsonify({"ok": True, "started": True, "running": True}), 202


@app.post("/api/live/stop")
def stop_live_preview():
    with _preview_start_lock:
        stopped = _stop_live_preview()
    return jsonify({"ok": True, "stopped": stopped})


@app.get("/api/live/<int:channel>")
def live_preview(channel: int):
    global _preview_process, _preview_generation

    if channel not in (0, 1):
        return jsonify({"error": "Unsupported preview channel"}), 404

    with _state_lock:
        if not _state.get("online") or not _state.get("authenticated"):
            return jsonify({"error": "Camera is not ready"}), 503
        if _state.get("probe_running") or _state.get("preview_probe_running"):
            return jsonify({"error": "Camera diagnostics are running"}), 409

    # Serialize preview replacement so concurrent browser reconnects can never
    # leave two RTSP/ffmpeg preview sessions alive at the same time.
    with _preview_start_lock:
        _stop_live_preview()

        if not _camera_io_lock.acquire(timeout=CAMERA_IO_LOCK_TIMEOUT):
            return jsonify({"error": "Camera is busy"}), 409
        try:
            path, source = _select_preview_stream(channel)
            stream = path.rsplit("/", 1)[-1]
            process = get_camera().start_mjpeg_preview(stream, width=640, fps=6)
        except Exception as exc:
            return jsonify({"error": redact_secrets(exc)}), 502
        finally:
            _camera_io_lock.release()

        with _preview_state_lock:
            _preview_generation += 1
            generation = _preview_generation
            _preview_process = process

        update_state(
            preview_active=True,
            preview_channel=channel,
            preview_stream=path,
            preview_source=source,
        )

    def generate():
        global _preview_process
        try:
            if process.stdout is None:
                return
            while True:
                chunk = process.stdout.read(16384)
                if not chunk:
                    break
                yield chunk
        finally:
            _terminate_process(process)
            with _preview_state_lock:
                if generation == _preview_generation and _preview_process is process:
                    _preview_process = None
                    update_state(
                        preview_active=False,
                        preview_channel=None,
                        preview_stream=None,
                        preview_source=None,
                    )

    return Response(
        generate(),
        content_type="multipart/x-mixed-replace;boundary=jooanframe",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Accel-Buffering": "no",
            "X-JOOAN-Preview-Stream": path,
            "X-JOOAN-Preview-Source": source,
        },
    )


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
        if _state.get("preview_active") or _state.get("preview_probe_running"):
            return jsonify({
                "ok": False,
                "error": "Stop live preview/preview validation before deep diagnostics",
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
            preview_active = bool(_state.get("preview_active"))
        if ptz_moving or preview_active:
            if cached is not None:
                return cached, True
            reason = "PTZ is moving" if ptz_moving else "live preview is active"
            raise RuntimeError(f"Snapshot paused while {reason}")

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
