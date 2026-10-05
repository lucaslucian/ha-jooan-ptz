from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from camera import JooanAuthError, JooanCamera, redact_secrets
from probe import onvif_continuous_move, onvif_ptz_discovery


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
_ptz_onvif_disabled_until = 0.0
_ptz_onvif_discovery_attempted = False
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
    "last_error": None,
    "last_check": None,
    "last_seen": None,
    "last_heartbeat": None,
    "heartbeat_error": None,
    "probe_running": False,
    "last_recovery_validation": None,
    "ptz_moving": False,
    "ptz_channel": None,
    "ptz_channel_source": None,
    "ptz_onvif_available": False,
    "ptz_last_transport": None,
    "ptz_last_error": None,
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


def _infer_ptz_channel(onvif_info: dict | None) -> tuple[int | None, str | None]:
    """Map the ONVIF PTZ profile back to the RTSP channel it controls."""
    onvif_info = onvif_info or {}
    ptz_profile = (onvif_info.get("ptz") or {}).get("profile_token")
    if not ptz_profile:
        return None, None

    for profile in onvif_info.get("profiles", []):
        if profile.get("token") != ptz_profile:
            continue
        path = (profile.get("stream") or {}).get("path")
        if not isinstance(path, str):
            continue
        match = re.fullmatch(r"/live/ch(\d{2})_[01]", path)
        if match:
            channel = int(match.group(1))
            if channel in (0, 1):
                return channel, "onvif_profile_mapping"
    return None, None


def validate_camera() -> bool:
    # All camera service traffic is serialized. The stock JA-A12 has very
    # limited HTTP/RTSP/ONVIF workers and concurrent protocol operations can
    # make otherwise valid requests time out.
    with _camera_io_lock:
        return _validate_camera_locked()


def _validate_camera_locked() -> bool:
    _LOGGER.info("Validating JOOAN camera over the local network")
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

        with _state_lock:
            previous_services = dict(_state.get("services") or {})

        services = {
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
                "reachable": None,
                "configured": bool((stream_info or {}).get("credentials_confirmed")),
                "source": "credentials_only",
            },
            "onvif": dict(
                previous_services.get("onvif")
                or {
                    "port": camera.onvif_port,
                    "reachable": None,
                    "source": "not_probed",
                }
            ),
        }

        values["services"] = services

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
    # Startup reads only the proven CGI/OEM state. ONVIF is discovered lazily
    # by the first PTZ movement and RTSP opens only for requested snapshots.
    update_state(probe_running=True)
    try:
        validate_camera()
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
            validate_camera()


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
    global _ptz_onvif_disabled_until, _ptz_onvif_discovery_attempted

    if direction not in PTZ_DIRECTIONS:
        return jsonify({"error": "Unsupported PTZ command"}), 400

    client_id, sequence, accepted = _register_ptz_sequence()
    if not accepted:
        if client_id is not None and sequence is not None:
            return jsonify({"ok": True, "ignored": True, "reason": "stale PTZ request"})
        return jsonify({"error": "Invalid PTZ sequence"}), 400

    try:
        speed = max(0.1, min(float(request.args.get("speed", 0.4)), 1.0))
        duration_ms = max(120, min(int(request.args.get("duration_ms", 280)), 600))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid PTZ speed/duration"}), 400

    with _state_lock:
        if not _state["online"] or not _state["authenticated"]:
            return jsonify({"error": "Camera is not ready"}), 503
        onvif_info = dict(_state.get("onvif_info") or {})
        onvif_available = bool(_state.get("ptz_onvif_available"))
        previous_transport = _state.get("ptz_last_transport")

    if not _camera_io_lock.acquire(timeout=CAMERA_IO_LOCK_TIMEOUT):
        return jsonify({"error": "Camera is busy with another operation"}), 503
    try:
        # A newer STOP/direction may have arrived while this request waited for
        # the camera lock. Never execute an older direction after a newer STOP.
        if not _ptz_sequence_is_current(client_id, sequence):
            return jsonify({"ok": True, "ignored": True, "reason": "superseded PTZ request"})

        camera = get_camera()
        onvif_error = None

        # ONVIF is not exposed as a separate diagnostic action anymore. The
        # first real PTZ movement performs one minimal capabilities/profiles
        # discovery. If the camera does not provide usable ONVIF PTZ, all
        # subsequent movement stays on the already-proven CGI transport.
        if (
            direction != "stop"
            and not onvif_available
            and not _ptz_onvif_discovery_attempted
        ):
            _ptz_onvif_discovery_attempted = True
            try:
                discovered = onvif_ptz_discovery(
                    camera.ip,
                    camera.onvif_port,
                )
                onvif_info = discovered
                onvif_available = bool(
                    ((discovered.get("ptz") or {}).get("profile_token"))
                )
                with _state_lock:
                    services = dict(_state.get("services") or {})
                services["onvif"] = {
                    "port": camera.onvif_port,
                    "reachable": bool(discovered.get("reachable")),
                    "source": "ptz_lazy_discovery",
                }
                update_state(
                    onvif_info=discovered,
                    ptz_onvif_available=onvif_available,
                    services=services,
                )
            except Exception as exc:
                onvif_error = redact_secrets(exc)
                with _state_lock:
                    services = dict(_state.get("services") or {})
                services["onvif"] = {
                    "port": camera.onvif_port,
                    "reachable": False,
                    "source": "ptz_lazy_discovery",
                }
                update_state(
                    ptz_onvif_available=False,
                    ptz_last_error=str(onvif_error),
                    services=services,
                )

        # Prefer ONVIF only when the lazy discovery produced a usable PTZ
        # profile. A failed move is cooled down briefly and falls back to CGI.
        if (
            direction != "stop"
            and onvif_available
            and time.monotonic() >= _ptz_onvif_disabled_until
        ):
            try:
                onvif_result = onvif_continuous_move(
                    camera.ip,
                    camera.onvif_port,
                    onvif_info,
                    direction=direction,
                    speed=speed,
                    duration_ms=duration_ms,
                )
                if (onvif_result.get("start") or {}).get("accepted"):
                    update_state(
                        ptz_moving=False,
                        ptz_last_transport="onvif",
                        ptz_last_error=None,
                    )
                    return jsonify({
                        "ok": True,
                        "transport": "onvif",
                        "speed": speed,
                        "duration_ms": duration_ms,
                        "result": onvif_result,
                    })
                onvif_error = (
                    (onvif_result.get("start") or {}).get("fault")
                    or (onvif_result.get("start") or {}).get("error")
                    or "ONVIF ContinuousMove was not accepted"
                )
            except Exception as exc:
                onvif_error = redact_secrets(exc)

            _ptz_onvif_disabled_until = time.monotonic() + 60.0

        result = camera.command(direction)
        transport = (
            "cgi-fallback"
            if direction != "stop" and onvif_available
            else "cgi"
        )
        if direction == "stop" and previous_transport == "onvif":
            transport = "cgi-stop"
        update_state(
            ptz_moving=direction != "stop",
            ptz_last_transport=transport,
            ptz_last_error=str(onvif_error) if onvif_error else None,
        )
        return jsonify({
            "ok": True,
            "transport": transport,
            "speed": None,
            "result": result,
            "onvif_error": str(onvif_error) if onvif_error else None,
        })
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


def _snapshot_cache_key(stream: str) -> tuple[str, int, str]:
    config = load_config()
    return (
        str(config.get("camera_ip", "")),
        int(config.get("rtsp_port", 554)),
        stream,
    )


def _capture_snapshot_with_fallback(
    stream: str,
    *,
    ptz_preview: bool = False,
) -> tuple[bytes, bool]:
    """Capture one frame and keep a short-lived last-good image.

    Normal/manual snapshots share the camera I/O lock with diagnostics and PTZ.
    PTZ preview frames deliberately bypass that lock so a slow FFmpeg capture
    can never delay a STOP command. The snapshot lock still prevents multiple
    RTSP captures from being opened by this App at the same time.
    """
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

        camera_lock_acquired = False
        if not ptz_preview:
            camera_lock_acquired = _camera_io_lock.acquire(timeout=CAMERA_IO_LOCK_TIMEOUT)
            if not camera_lock_acquired:
                if cached is not None:
                    return cached, True
                raise RuntimeError("Camera is busy with another operation")

        try:
            image = get_camera().snapshot(stream)
        except Exception:
            if cached is not None:
                return cached, True
            raise
        finally:
            if camera_lock_acquired:
                _camera_io_lock.release()

        _snapshot_cache[key] = (time.monotonic(), image)
        return image, False


@app.get("/api/snapshot/<stream>")
def snapshot(stream: str):
    try:
        ptz_preview = request.args.get("ptz") == "1"
        image, stale = _capture_snapshot_with_fallback(
            stream,
            ptz_preview=ptz_preview,
        )
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
