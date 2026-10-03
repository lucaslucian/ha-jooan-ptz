from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import threading
import time
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from probe import (
    ONVIF_PTZ,
    _extract_presets,
    _local_name,
    _parse_xml,
    _safe_service_path,
    _soap_envelope,
    _soap_fault,
    _soap_post,
)

ONVIF_IMAGING = "http://www.onvif.org/ver20/imaging/wsdl"
ONVIF_EVENTS = "http://www.onvif.org/ver10/events/wsdl"
LAB_TEST_PRESET_NAME = "HA_TEST"
LAB_MAX_EVENT_MESSAGES = 32
LAB_MAX_XML_ROWS = 96
LAB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
LAB_DIRECTIONS = {"up", "down", "left", "right"}


class LabError(RuntimeError):
    """Raised when an experimental action cannot be executed safely."""


def _safe_token(value: object, *, label: str = "token") -> str:
    token = str(value or "")
    if not LAB_TOKEN_RE.fullmatch(token):
        raise LabError(f"Invalid {label}")
    return token


def _service_path(onvif_info: dict, name: str) -> str:
    direct = (onvif_info.get(name) or {}).get("path")
    discovered = ((onvif_info.get("services") or {}).get(name) or {}).get("path")
    path = direct or discovered
    path = _safe_service_path(path)
    if not path:
        raise LabError(f"ONVIF {name} service was not discovered")
    return path


def _ptz_context(onvif_info: dict) -> tuple[str, str]:
    path = _service_path(onvif_info, "ptz")
    token = (onvif_info.get("ptz") or {}).get("profile_token")
    if not token:
        profiles = onvif_info.get("profiles") or []
        token = profiles[0].get("token") if profiles else None
    return path, _safe_token(token, label="PTZ profile token")


def _video_source_token(onvif_info: dict) -> str:
    sources = onvif_info.get("video_sources") or []
    if not sources:
        raise LabError("ONVIF did not report a video source token")
    return _safe_token(sources[0].get("token"), label="video source token")


def _response_summary(response: dict[str, Any]) -> dict[str, Any]:
    root = _parse_xml(response.get("body") or b"")
    fault = _soap_fault(root)
    return {
        "http": response.get("status"),
        "authentication_required": response.get("authentication_required"),
        "accepted": response.get("status") == 200 and fault is None,
        "fault": fault,
        "error": response.get("error"),
    }


def _soap(
    camera,
    *,
    path: str,
    namespace: str,
    prefix: str,
    payload: str,
    action: str,
    timeout: float = 4.0,
) -> tuple[dict[str, Any], Any]:
    response = _soap_post(
        camera.ip,
        camera.onvif_port,
        path,
        _soap_envelope(namespace, prefix, payload),
        action=f"{namespace}/{action}",
        timeout=timeout,
    )
    return response, _parse_xml(response.get("body") or b"")


def _xml_rows(root, *, limit: int = LAB_MAX_XML_ROWS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if root is None:
        return rows
    for node in root.iter():
        if len(rows) >= limit:
            break
        text = (node.text or "").strip()
        attributes = {
            str(key): str(value)[:180]
            for key, value in node.attrib.items()
            if value is not None
        }
        if not text and not attributes:
            continue
        row: dict[str, Any] = {"name": _local_name(node.tag)}
        if text:
            row["value"] = text[:300]
        if attributes:
            row["attributes"] = attributes
        rows.append(row)
    return rows


def onvif_continuous_move(
    camera,
    onvif_info: dict,
    *,
    direction: str,
    speed: float = 0.25,
    duration_ms: int = 250,
) -> dict[str, Any]:
    """Run one bounded ONVIF move and always issue Stop afterwards."""
    if direction not in LAB_DIRECTIONS:
        raise LabError("Unsupported ONVIF PTZ direction")
    speed = max(0.1, min(float(speed), 1.0))
    duration_ms = max(80, min(int(duration_ms), 800))
    path, token = _ptz_context(onvif_info)

    vectors = {
        "up": (0.0, speed),
        "down": (0.0, -speed),
        "left": (-speed, 0.0),
        "right": (speed, 0.0),
    }
    x, y = vectors[direction]
    start_payload = (
        "<tptz:ContinuousMove>"
        f"<tptz:ProfileToken>{xml_escape(token)}</tptz:ProfileToken>"
        "<tptz:Velocity>"
        f'<tt:PanTilt x="{x:.3f}" y="{y:.3f}"/>'
        "</tptz:Velocity>"
        "</tptz:ContinuousMove>"
    )
    stop_payload = (
        "<tptz:Stop>"
        f"<tptz:ProfileToken>{xml_escape(token)}</tptz:ProfileToken>"
        "<tptz:PanTilt>true</tptz:PanTilt>"
        "<tptz:Zoom>true</tptz:Zoom>"
        "</tptz:Stop>"
    )

    start_response = None
    stop_response = None
    try:
        start_response, _ = _soap(
            camera,
            path=path,
            namespace=ONVIF_PTZ,
            prefix="tptz",
            payload=start_payload,
            action="ContinuousMove",
        )
        if start_response.get("status") == 200:
            time.sleep(duration_ms / 1000.0)
    finally:
        stop_response, _ = _soap(
            camera,
            path=path,
            namespace=ONVIF_PTZ,
            prefix="tptz",
            payload=stop_payload,
            action="Stop",
        )

    return {
        "operation": "onvif_continuous_move",
        "direction": direction,
        "speed": speed,
        "duration_ms": duration_ms,
        "start": _response_summary(start_response or {}),
        "stop": _response_summary(stop_response or {}),
    }


def onvif_ir_lamp(camera, onvif_info: dict, *, enabled: bool) -> dict[str, Any]:
    """Send only an IR auxiliary command explicitly advertised by the PTZ node."""
    path, token = _ptz_context(onvif_info)
    command = "tt:Irlamp|On" if enabled else "tt:Irlamp|Off"
    advertised = {
        str(item)
        for node in ((onvif_info.get("ptz") or {}).get("nodes") or [])
        for item in (node.get("auxiliary_commands") or [])
    }
    if command not in advertised:
        raise LabError("The camera did not advertise the requested IR auxiliary command")

    payload = (
        "<tptz:SendAuxiliaryCommand>"
        f"<tptz:ProfileToken>{xml_escape(token)}</tptz:ProfileToken>"
        f"<tptz:AuxiliaryData>{xml_escape(command)}</tptz:AuxiliaryData>"
        "</tptz:SendAuxiliaryCommand>"
    )
    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_PTZ,
        prefix="tptz",
        payload=payload,
        action="SendAuxiliaryCommand",
    )
    return {
        "operation": "onvif_ir_lamp",
        "requested": "on" if enabled else "off",
        "command": command,
        **_response_summary(response),
        "response": _xml_rows(root, limit=16),
    }


def onvif_list_presets(camera, onvif_info: dict) -> dict[str, Any]:
    path, token = _ptz_context(onvif_info)
    payload = (
        "<tptz:GetPresets>"
        f"<tptz:ProfileToken>{xml_escape(token)}</tptz:ProfileToken>"
        "</tptz:GetPresets>"
    )
    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_PTZ,
        prefix="tptz",
        payload=payload,
        action="GetPresets",
    )
    return {
        "operation": "onvif_list_presets",
        **_response_summary(response),
        "presets": _extract_presets(root) if response.get("status") == 200 else [],
    }


def onvif_create_test_preset(camera, onvif_info: dict) -> dict[str, Any]:
    existing = onvif_list_presets(camera, onvif_info)
    for preset in existing.get("presets", []):
        if preset.get("name") == LAB_TEST_PRESET_NAME:
            return {
                "operation": "onvif_create_test_preset",
                "accepted": True,
                "reused": True,
                "preset": preset,
            }

    path, profile_token = _ptz_context(onvif_info)
    payload = (
        "<tptz:SetPreset>"
        f"<tptz:ProfileToken>{xml_escape(profile_token)}</tptz:ProfileToken>"
        f"<tptz:PresetName>{LAB_TEST_PRESET_NAME}</tptz:PresetName>"
        "</tptz:SetPreset>"
    )
    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_PTZ,
        prefix="tptz",
        payload=payload,
        action="SetPreset",
    )
    token = None
    if root is not None:
        for node in root.iter():
            if _local_name(node.tag) == "PresetToken" and node.text:
                token = node.text.strip()
                break

    result = {
        "operation": "onvif_create_test_preset",
        "name": LAB_TEST_PRESET_NAME,
        **_response_summary(response),
        "preset_token": token,
    }
    if result["accepted"]:
        result["readback"] = onvif_list_presets(camera, onvif_info)
    return result


def _current_preset(camera, onvif_info: dict, token: str) -> dict[str, Any]:
    token = _safe_token(token, label="preset token")
    listing = onvif_list_presets(camera, onvif_info)
    if not listing.get("accepted"):
        raise LabError("Could not read current preset list")
    match = next((item for item in listing.get("presets", []) if str(item.get("token")) == token), None)
    if match is None:
        raise LabError("Preset token was not returned by the camera")
    return match


def onvif_goto_preset(camera, onvif_info: dict, *, token: str) -> dict[str, Any]:
    preset = _current_preset(camera, onvif_info, token)
    path, profile_token = _ptz_context(onvif_info)
    payload = (
        "<tptz:GotoPreset>"
        f"<tptz:ProfileToken>{xml_escape(profile_token)}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{xml_escape(str(preset['token']))}</tptz:PresetToken>"
        "</tptz:GotoPreset>"
    )
    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_PTZ,
        prefix="tptz",
        payload=payload,
        action="GotoPreset",
    )
    return {
        "operation": "onvif_goto_preset",
        "preset": preset,
        **_response_summary(response),
        "response": _xml_rows(root, limit=16),
    }


def onvif_delete_test_preset(camera, onvif_info: dict, *, token: str) -> dict[str, Any]:
    preset = _current_preset(camera, onvif_info, token)
    if preset.get("name") != LAB_TEST_PRESET_NAME:
        raise LabError("Only the HA_TEST preset can be deleted from the laboratory")

    path, profile_token = _ptz_context(onvif_info)
    payload = (
        "<tptz:RemovePreset>"
        f"<tptz:ProfileToken>{xml_escape(profile_token)}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{xml_escape(str(preset['token']))}</tptz:PresetToken>"
        "</tptz:RemovePreset>"
    )
    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_PTZ,
        prefix="tptz",
        payload=payload,
        action="RemovePreset",
    )
    result = {
        "operation": "onvif_delete_test_preset",
        "preset": preset,
        **_response_summary(response),
        "response": _xml_rows(root, limit=16),
    }
    if result["accepted"]:
        result["readback"] = onvif_list_presets(camera, onvif_info)
    return result


def onvif_imaging_discovery(camera, onvif_info: dict) -> dict[str, Any]:
    """Read current imaging settings/options before any write experiment exists."""
    path = _service_path(onvif_info, "imaging")
    source_token = _video_source_token(onvif_info)
    operations = (
        (
            "settings",
            "GetImagingSettings",
            "<timg:GetImagingSettings>"
            f"<timg:VideoSourceToken>{xml_escape(source_token)}</timg:VideoSourceToken>"
            "</timg:GetImagingSettings>",
        ),
        (
            "options",
            "GetOptions",
            "<timg:GetOptions>"
            f"<timg:VideoSourceToken>{xml_escape(source_token)}</timg:VideoSourceToken>"
            "</timg:GetOptions>",
        ),
    )
    result: dict[str, Any] = {
        "operation": "onvif_imaging_discovery",
        "video_source_token": source_token,
    }
    for key, action, payload in operations:
        response, root = _soap(
            camera,
            path=path,
            namespace=ONVIF_IMAGING,
            prefix="timg",
            payload=payload,
            action=action,
        )
        result[key] = {
            **_response_summary(response),
            "values": _xml_rows(root),
        }
        if response.get("status") is None or response.get("status") == 401:
            break
    return result


def onvif_event_discovery(camera, onvif_info: dict) -> dict[str, Any]:
    path = _service_path(onvif_info, "events")
    operations = (
        ("service_capabilities", "GetServiceCapabilities", "<tev:GetServiceCapabilities/>"),
        ("event_properties", "GetEventProperties", "<tev:GetEventProperties/>"),
    )
    result: dict[str, Any] = {"operation": "onvif_event_discovery"}
    for key, action, payload in operations:
        response, root = _soap(
            camera,
            path=path,
            namespace=ONVIF_EVENTS,
            prefix="tev",
            payload=payload,
            action=action,
        )
        result[key] = {
            **_response_summary(response),
            "values": _xml_rows(root),
        }
        if response.get("status") is None or response.get("status") == 401:
            break
    return result


def _subscription_path(root, fallback: str) -> str:
    if root is not None:
        for node in root.iter():
            if _local_name(node.tag) == "Address" and node.text:
                path = _safe_service_path(node.text.strip())
                if path:
                    return path
    return fallback


def _notification_messages(root) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if root is None:
        return messages
    for node in root.iter():
        if _local_name(node.tag) != "NotificationMessage":
            continue
        topic = None
        items: list[dict[str, str]] = []
        for child in node.iter():
            local = _local_name(child.tag)
            if local == "Topic" and child.text:
                topic = child.text.strip()[:240]
            elif local == "SimpleItem":
                name = str(child.attrib.get("Name") or "")[:120]
                value = str(child.attrib.get("Value") or "")[:240]
                if name:
                    items.append({"name": name, "value": value})
                    if len(items) >= 32:
                        break
        messages.append({"topic": topic, "items": items})
        if len(messages) >= LAB_MAX_EVENT_MESSAGES:
            break
    return messages


def onvif_pull_events(camera, onvif_info: dict) -> dict[str, Any]:
    """Create a short-lived PullPoint subscription and pull one batch.

    The subscription requests a 15-second lifetime and is intentionally allowed
    to expire instead of leaving a persistent listener on the camera.
    """
    events_path = _service_path(onvif_info, "events")
    create_response, create_root = _soap(
        camera,
        path=events_path,
        namespace=ONVIF_EVENTS,
        prefix="tev",
        payload=(
            "<tev:CreatePullPointSubscription>"
            "<tev:InitialTerminationTime>PT15S</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>"
        ),
        action="CreatePullPointSubscription",
        timeout=5.0,
    )
    create_summary = _response_summary(create_response)
    if not create_summary["accepted"]:
        return {
            "operation": "onvif_pull_events",
            "subscription": create_summary,
            "messages": [],
        }

    pull_path = _subscription_path(create_root, events_path)
    pull_response, pull_root = _soap(
        camera,
        path=pull_path,
        namespace=ONVIF_EVENTS,
        prefix="tev",
        payload=(
            "<tev:PullMessages>"
            "<tev:Timeout>PT5S</tev:Timeout>"
            "<tev:MessageLimit>16</tev:MessageLimit>"
            "</tev:PullMessages>"
        ),
        action="PullMessages",
        timeout=7.0,
    )
    return {
        "operation": "onvif_pull_events",
        "subscription": create_summary,
        "pull": _response_summary(pull_response),
        "subscription_path": pull_path,
        "messages": _notification_messages(pull_root),
    }


def validate_diag_callback_ip(camera_ip: str, callback_ip: str) -> str:
    """Allow only a private callback address on the camera's local subnet."""
    try:
        camera_addr = ipaddress.ip_address(str(camera_ip))
        callback_addr = ipaddress.ip_address(str(callback_ip or "").strip())
    except ValueError as exc:
        raise LabError("Diagnostic callback IP must be a literal local IP") from exc

    if camera_addr.version != callback_addr.version:
        raise LabError("Diagnostic callback IP must use the same address family as the camera")
    if callback_addr.is_loopback or callback_addr.is_multicast or callback_addr.is_unspecified:
        raise LabError("Diagnostic callback IP must be a reachable LAN address")

    if callback_addr.version == 4:
        allowed = (
            callback_addr in ipaddress.ip_network("10.0.0.0/8")
            or callback_addr in ipaddress.ip_network("172.16.0.0/12")
            or callback_addr in ipaddress.ip_network("192.168.0.0/16")
        )
        if not allowed:
            raise LabError("Diagnostic callback IP must be RFC1918")
        camera_net = ipaddress.ip_network(f"{camera_addr}/24", strict=False)
    else:
        allowed = callback_addr in ipaddress.ip_network("fc00::/7")
        if not allowed:
            raise LabError("Diagnostic callback IP must be ULA")
        camera_net = ipaddress.ip_network(f"{camera_addr}/64", strict=False)

    if callback_addr not in camera_net:
        raise LabError("Diagnostic callback IP must be on the same local subnet as the camera")
    if callback_addr == camera_addr:
        raise LabError("Diagnostic callback IP cannot be the camera itself")
    return str(callback_addr)


def safe_diag_mode_callback_probe(
    camera,
    *,
    callback_ip: str,
    callback_port: int = 49000,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Arm SetDiagMode only long enough to prove a callback reaches our sink.

    The sink accepts a connection only from the configured camera IP and closes
    it without reading, writing or forwarding a protocol payload. The browser
    cannot choose callback host/port/authcode and the mode is forced OFF in a
    finally block.
    """
    safe_ip = validate_diag_callback_ip(camera.ip, callback_ip)
    port = int(callback_port)
    if port != 49000:
        raise LabError("Diagnostic laboratory callback port is fixed to 49000")

    family = socket.AF_INET6 if ":" in safe_ip else socket.AF_INET
    bind_host = "::" if family == socket.AF_INET6 else "0.0.0.0"
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind_host, port))
    listener.listen(4)
    listener.settimeout(0.5)

    callback_seen = threading.Event()
    stop_accept = threading.Event()
    callback_peer: list[str] = []

    def accept_sink() -> None:
        deadline = time.monotonic() + max(1.0, min(float(timeout), 10.0))
        while not stop_accept.is_set() and time.monotonic() < deadline:
            try:
                conn, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                peer_ip = str(peer[0])
                if peer_ip == camera.ip:
                    callback_peer.append(peer_ip)
                    callback_seen.set()
                    # Deliberately send no bytes and accept no command payload.
                    return
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    thread = threading.Thread(target=accept_sink, name="safe-diag-sink", daemon=True)
    thread.start()

    authcode = f"{secrets.randbelow(1_000_000):06d}"
    authtime = int(time.time()) + 60
    enable_result = None
    enable_error = None
    disable_result = None
    try:
        try:
            enable_result = camera._goform(
                "/goform/SingleHandlebyCommand",
                {
                    "singleCMD": "SetDiagMode",
                    "enable": "1",
                    "authcode": authcode,
                    "authtime": str(authtime),
                    "authserverip": safe_ip,
                    "authserverport": str(port),
                },
            )
        except Exception as exc:
            # Do not return the exception text because request URLs may contain
            # ephemeral diagnostic authorization material.
            enable_error = type(exc).__name__
        callback_seen.wait(max(1.0, min(float(timeout), 10.0)))
    finally:
        try:
            disable_result = camera._goform(
                "/goform/SingleHandlebyCommand",
                {"singleCMD": "SetDiagMode", "enable": "0"},
            )
        except Exception:
            disable_result = {"result": "disable_request_failed"}
        stop_accept.set()
        try:
            listener.close()
        except OSError:
            pass
        thread.join(timeout=1.0)

    enable_status = str((enable_result or {}).get("result") or "").lower()
    disable_status = str((disable_result or {}).get("result") or "").lower()
    return {
        "operation": "diag_mode_safe_callback_probe",
        "callback_seen": callback_seen.is_set(),
        "callback_peer_verified": bool(callback_peer),
        "enable_accepted": enable_status in {"success", "successful", "ok"},
        "enable_error_type": enable_error,
        "disable_accepted": disable_status in {"success", "successful", "ok"},
        "callback_port": port,
    }


def disable_diag_mode(camera) -> dict[str, Any]:
    """Force the OEM diagnostic callback mode off.

    This is the only SetDiagMode operation exposed by the initial laboratory.
    It never accepts an auth server, port, authorization code or payload from
    the browser, so it cannot be used as a generic callback/shell primitive.
    """
    data = camera._goform(  # deliberately fixed, no browser-controlled singleCMD
        "/goform/SingleHandlebyCommand",
        {"singleCMD": "SetDiagMode", "enable": "0"},
    )
    return {
        "operation": "diag_mode_disable",
        "accepted": str(data.get("result", "")).lower() in {"success", "successful", "ok"},
        "result": data.get("result"),
    }
