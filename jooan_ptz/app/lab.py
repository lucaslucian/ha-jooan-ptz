from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit
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
ONVIF_RECORDING = "http://www.onvif.org/ver10/recording/wsdl"
ONVIF_SEARCH = "http://www.onvif.org/ver10/search/wsdl"
ONVIF_REPLAY = "http://www.onvif.org/ver10/replay/wsdl"
LAB_TEST_PRESET_NAME = "HA_TEST"
LAB_MAX_EVENT_MESSAGES = 64
LAB_MAX_XML_ROWS = 96
LAB_MAX_PULL_SECONDS = 30
LAB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
LAB_DIRECTIONS = {"up", "down", "left", "right"}
LAB_IMAGING_FIELDS = {"Brightness", "ColorSaturation", "Contrast", "Sharpness"}
OEM_RECORDING_KEYS = (
    "record_enable",
    "record_type",
    "rectype",
    "recordechannel",
    "recloopnum",
    "record_schedule",
    "newrecord_schedule",
    "sdcard_status",
    "sdcard_excepreason",
    "sdcard_total",
    "sdcard_free",
    "lastformattime",
    "week",
)

OEM_TOGGLE_GROUPS: dict[str, tuple[str, ...]] = {
    "motion": (
        "md_enable",
        "sub_md_enable",
        "mdsensitivity",
        "sub_mdsensitivity",
        "mdarea",
        "sub_mdarea",
    ),
    "smart_detection": (
        "person_detect",
        "vehicle_detect",
        "pdarea",
    ),
    "tracking": (
        "autotrack",
        "person_track_enable",
    ),
    "lighting": (
        "led",
        "floodlight",
        "yellowlight",
        "alarm_light_switch",
        "alarm_light_mode",
        "light_schedule",
        "newflood_light_schedule",
    ),
    "alerts": (
        "msgpush_enable",
        "audiosensitive",
        "buzzer",
        "alarmsoundselect",
        "msgpush_schedule",
        "newmsg_push_schedule",
        "sound_alarm_schedule",
    ),
    "privacy": (
        "ptz_hide_mode",
        "ptz_covre_status",
        "ptz_hide_schedule",
        "flipmirror",
    ),
    "system": (
        "timezone",
        "powerfrequency",
        "video_standard_red",
        "qualitymode",
        "definition",
        "resolution",
    ),
}


OEM_WRITE_PROPERTY_KEYS = (
    "floodlight",
    "autotrack",
    "flipmirror",
    "mdarea",
    "sub_mdarea",
    "mdsensitivity",
    "sub_mdsensitivity",
    "timezone",
)
OEM_MOTION_ALL_ZONES = (1 << 25) - 1
OEM_TIMEZONE_RE = re.compile(r"^GMT[+-](?:0\d|1[0-4]):(?:00|15|30|45)$")


class LabError(RuntimeError):
    """Raised when an experimental action cannot be executed safely."""


OEM_FINGERPRINT_DENY_SUBSTRINGS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "userkey",
    "authkey",
    "auth_key",
    "credential",
    "token",
    "private",
    "ssid",
    "wifi",
    "wlan",
)


def _oem_property_fingerprints(properties: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Fingerprint non-sensitive OEM properties without returning their values.

    The wide fingerprint map is used only to discover that an unknown property
    changed outside the selected allowlisted group. Sensitive/network-looking
    keys are omitted entirely.
    """
    result: dict[str, dict[str, str]] = {}
    for raw_key, value in properties.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(part in lowered for part in OEM_FINGERPRINT_DENY_SUBSTRINGS):
            continue
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        except Exception:
            encoded = repr(type(value).__name__).encode("utf-8")
        result[key] = {
            "fingerprint": hashlib.sha256(encoded).hexdigest()[:16],
            "type": type(value).__name__,
        }
        if len(result) >= 256:
            break
    return result


def _coerce_oem_write_plan(target: str, value: Any) -> dict[str, Any]:
    """Validate one strictly allowlisted OEM write target.

    This function describes a desired mutation only. It intentionally does not
    select or guess a transport; a stock local configuration writer has not yet
    been proven for this firmware.
    """
    target = str(target or "").strip()

    if target == "floodlight":
        try:
            mode = int(value)
        except (TypeError, ValueError) as exc:
            raise LabError("floodlight must be 0, 1, 2 or 3") from exc
        if mode not in {0, 1, 2, 3}:
            raise LabError("floodlight must be 0, 1, 2 or 3")
        return {"floodlight": mode}

    if target == "autotrack":
        if value in (True, 1, "1", "on", "true"):
            enabled = 1
        elif value in (False, 0, "0", "off", "false"):
            enabled = 0
        else:
            raise LabError("autotrack must be on/off")
        return {"autotrack": enabled}

    if target == "flipmirror":
        if value in (True, 3, "3", "on", "true"):
            enabled = 3
        elif value in (False, 0, "0", "off", "false"):
            enabled = 0
        else:
            raise LabError("flipmirror must be on/off")
        return {"flipmirror": enabled}

    if target == "motion_zones":
        normalized = str(value or "").strip().lower()
        if normalized in {"on", "all", "1", "true"}:
            mask = OEM_MOTION_ALL_ZONES
        elif normalized in {"off", "none", "0", "false"}:
            mask = 0
        else:
            raise LabError("motion_zones must be on/off")
        return {"mdarea": mask, "sub_mdarea": mask}

    if target == "motion_sensitivity":
        try:
            sensitivity = int(value)
        except (TypeError, ValueError) as exc:
            raise LabError("motion_sensitivity must be 1, 2 or 3") from exc
        if sensitivity not in {1, 2, 3}:
            raise LabError("motion_sensitivity must be 1, 2 or 3")
        return {
            "mdsensitivity": sensitivity,
            "sub_mdsensitivity": sensitivity,
        }

    if target == "timezone":
        timezone = str(value or "").strip().upper()
        if not OEM_TIMEZONE_RE.fullmatch(timezone):
            raise LabError("timezone must use the confirmed GMT±HH:MM format")
        return {"timezone": timezone}

    raise LabError("Unsupported OEM write target")


def oem_write_plan(camera, *, target: str, value: Any) -> dict[str, Any]:
    """Build a mutation plan from confirmed value mappings without writing."""
    requested = _coerce_oem_write_plan(target, value)
    info = camera.get_device_features().as_dict()
    properties = info.get("properties") or {}
    before = {
        key: properties.get(key)
        for key in requested
        if key in properties
    }
    differences = {
        key: {"before": properties.get(key), "after": expected}
        for key, expected in requested.items()
        if properties.get(key) != expected
    }
    return {
        "operation": "oem_write_plan",
        "target": str(target or "").strip(),
        "requested": requested,
        "before": before,
        "differences": differences,
        "would_change": bool(differences),
        "writer_ready": False,
        "executed": False,
        "reason": (
            "No stock local OEM configuration setter has been evidenced yet; "
            "the laboratory will not guess a Set* command."
        ),
    }


def _safe_get_json_conf_summary(data: Any) -> dict[str, Any]:
    """Return only non-secret fields from the fixed GetJsonConf probe."""
    if not isinstance(data, dict):
        return {"parsed": False}

    result: dict[str, Any] = {"parsed": True}
    if "result" in data:
        result["result"] = data.get("result")

    system = data.get("SystemInfo")
    if isinstance(system, dict) and "ProductName" in system:
        result["product_name"] = system.get("ProductName")

    # Some firmwares wrap the query result under another object/string. Search
    # only for ProductName and never echo the full configuration response.
    if "product_name" not in result:
        def find_product_name(node: Any, depth: int = 0) -> Any:
            if depth > 4:
                return None
            if isinstance(node, dict):
                for key, item in node.items():
                    if str(key) == "ProductName" and isinstance(item, (str, int, float)):
                        return item
                    found = find_product_name(item, depth + 1)
                    if found is not None:
                        return found
            elif isinstance(node, list):
                for item in node[:16]:
                    found = find_product_name(item, depth + 1)
                    if found is not None:
                        return found
            return None

        product = find_product_name(data)
        if product is not None:
            result["product_name"] = product
    return result


def oem_write_surface_probe(camera) -> dict[str, Any]:
    """Inspect only evidenced OEM configuration surfaces; never mutate state."""
    info = camera.get_device_features().as_dict()
    properties = info.get("properties") or {}
    current = {
        key: properties.get(key)
        for key in OEM_WRITE_PROPERTY_KEYS
        if key in properties
    }

    get_json_conf: dict[str, Any]
    try:
        response = camera._goform(
            "/goform/getOtherSetttings",
            {
                "singleCMD": "GetJsonConf",
                "json_string": json.dumps(
                    {"SystemInfo": {"ProductName": ""}},
                    separators=(",", ":"),
                ),
            },
        )
        get_json_conf = {
            "accepted": True,
            **_safe_get_json_conf_summary(response),
        }
    except Exception as exc:
        get_json_conf = {
            "accepted": False,
            "error_type": type(exc).__name__,
        }

    return {
        "operation": "oem_write_surface_probe",
        "readback_9898": current,
        "get_json_conf": get_json_conf,
        "configuration_writer_discovered": False,
        "write_transports": {
            "cgi_single_command": {
                "known_writable": True,
                "scope": ["PTZ", "SetDiagMode"],
                "configuration_setter_known": False,
            },
            "features_9898": {
                "known_read": "GET /get?singleCMD=get_deviceFeatures",
                "configuration_setter_known": False,
            },
            "get_json_conf": {
                "known_read": "GetJsonConf with a fixed SystemInfo/ProductName query",
                "configuration_setter_known": False,
            },
        },
        "next_step": (
            "Capture or reverse the exact CAM720 configuration write request. "
            "No arbitrary CGI, DP/MQTT or diagnostic-shell command is attempted."
        ),
    }


def oem_toggle_snapshot(camera, *, group: str) -> dict[str, Any]:
    """Read one allowlisted OEM feature group from the stock 9898 endpoint.

    This is deliberately read-only. It exists to map the effect of changing a
    single CAM720 option before any local setter is implemented.
    """
    group = str(group or "").strip()
    keys = OEM_TOGGLE_GROUPS.get(group)
    if not keys:
        raise LabError("Unsupported OEM toggle group")

    info = camera.get_device_features().as_dict()
    properties = info.get("properties") or {}
    values = {key: properties.get(key) for key in keys if key in properties}
    return {
        "operation": "oem_toggle_snapshot",
        "captured_at": int(time.time()),
        "group": group,
        "keys": list(keys),
        "values": values,
        "present_count": len(values),
        "wide_fingerprints": _oem_property_fingerprints(properties),
        "capabilities": info.get("capabilities") or {},
    }


def oem_recording_snapshot(camera) -> dict[str, Any]:
    """Read only OEM recording/SD fields from the stock 9898 feature endpoint."""
    info = camera.get_device_features().as_dict()
    properties = info.get("properties") or {}
    snapshot = {
        key: properties.get(key)
        for key in OEM_RECORDING_KEYS
        if key in properties
    }
    features = info.get("device_features") or {}
    return {
        "operation": "oem_recording_snapshot",
        "captured_at": int(time.time()),
        "properties": snapshot,
        "playback_fast_forward": features.get("10043"),
        "channel_count": info.get("channel_count"),
        "capabilities": {
            "recording": bool((info.get("capabilities") or {}).get("recording")),
            "sdcard": bool((info.get("capabilities") or {}).get("sdcard")),
        },
    }


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


def _services_list(onvif_info: dict) -> list[dict[str, Any]]:
    diagnostics = onvif_info.get("device_diagnostics") or {}
    entry = diagnostics.get("services_list") or {}
    data = entry.get("data") or []
    return [item for item in data if isinstance(item, dict)]


def _service_path_by_namespace(onvif_info: dict, namespace: str) -> str | None:
    for service in _services_list(onvif_info):
        if service.get("namespace") == namespace:
            path = _safe_service_path(service.get("path"))
            if path:
                return path
    return None


def _extract_ranges(root) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    if root is None:
        return ranges
    for node in root.iter():
        direct: dict[str, str] = {}
        for child in list(node):
            local = _local_name(child.tag)
            if local in {"Min", "Max"} and child.text:
                direct[local.lower()] = child.text.strip()
        if "min" in direct and "max" in direct:
            ranges.append({
                "name": _local_name(node.tag),
                "min": direct["min"],
                "max": direct["max"],
            })
            if len(ranges) >= 32:
                break
    return ranges


def _xml_tree(node, *, depth: int = 0, max_depth: int = 5) -> dict[str, Any] | None:
    if node is None or depth > max_depth:
        return None
    result: dict[str, Any] = {"name": _local_name(node.tag)}
    text = (node.text or "").strip()
    if text:
        result["value"] = text[:300]
    if node.attrib:
        result["attributes"] = {
            str(key): str(value)[:180]
            for key, value in node.attrib.items()
            if value is not None
        }
    children = []
    for child in list(node)[:64]:
        parsed = _xml_tree(child, depth=depth + 1, max_depth=max_depth)
        if parsed:
            children.append(parsed)
    if children:
        result["children"] = children
    return result


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
            "ranges": _extract_ranges(root),
            "hierarchy": _xml_tree(root),
        }
        if response.get("status") is None or response.get("status") == 401:
            break
    return result


def onvif_imaging_path_probe(camera, onvif_info: dict) -> dict[str, Any]:
    """Compare capability-derived and GetServices-advertised Imaging paths read-only."""
    source_token = _video_source_token(onvif_info)
    candidates: list[dict[str, str]] = []

    capability_path = None
    try:
        capability_path = _service_path(onvif_info, "imaging")
    except LabError:
        pass
    if capability_path:
        candidates.append({"source": "GetCapabilities", "path": capability_path})

    services_path = _service_path_by_namespace(onvif_info, ONVIF_IMAGING)
    if services_path and all(item["path"] != services_path for item in candidates):
        candidates.append({"source": "GetServices", "path": services_path})

    if not candidates:
        raise LabError("No ONVIF Imaging path was discovered")

    result: dict[str, Any] = {
        "operation": "onvif_imaging_path_probe",
        "video_source_token": source_token,
        "paths": [],
    }
    for candidate in candidates[:2]:
        path = candidate["path"]
        entry: dict[str, Any] = dict(candidate)
        operations = (
            (
                "service_capabilities",
                "GetServiceCapabilities",
                "<timg:GetServiceCapabilities/>",
            ),
            (
                "options",
                "GetOptions",
                "<timg:GetOptions>"
                f"<timg:VideoSourceToken>{xml_escape(source_token)}</timg:VideoSourceToken>"
                "</timg:GetOptions>",
            ),
            (
                "settings",
                "GetImagingSettings",
                "<timg:GetImagingSettings>"
                f"<timg:VideoSourceToken>{xml_escape(source_token)}</timg:VideoSourceToken>"
                "</timg:GetImagingSettings>",
            ),
        )
        for key, action, payload in operations:
            response, root = _soap(
                camera,
                path=path,
                namespace=ONVIF_IMAGING,
                prefix="timg",
                payload=payload,
                action=action,
            )
            entry[key] = {
                **_response_summary(response),
                "ranges": _extract_ranges(root),
                "values": _xml_rows(root, limit=48),
            }
            if response.get("status") is None or response.get("status") == 401:
                break
        result["paths"].append(entry)

    result["alternate_path_present"] = len(result["paths"]) > 1
    result["any_settings_readable"] = any(
        bool((item.get("settings") or {}).get("accepted"))
        for item in result["paths"]
    )
    return result


def onvif_set_imaging(
    camera,
    onvif_info: dict,
    *,
    setting: str,
    value: int,
) -> dict[str, Any]:
    """Write one allowlisted imaging value within the range proven by GetOptions.

    GetImagingSettings is not implemented by the validated stock firmware, so
    this experiment intentionally changes only one optional setting at a time
    and requests non-persistent application. There is no automatic readback.
    """
    setting = str(setting or "")
    if setting not in LAB_IMAGING_FIELDS:
        raise LabError("Unsupported imaging setting")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise LabError("Imaging value must be an integer") from exc
    if not 1 <= value <= 255:
        raise LabError("Imaging value must be between 1 and 255")

    path = _service_path(onvif_info, "imaging")
    source_token = _video_source_token(onvif_info)
    payload = (
        "<timg:SetImagingSettings>"
        f"<timg:VideoSourceToken>{xml_escape(source_token)}</timg:VideoSourceToken>"
        "<timg:ImagingSettings>"
        f"<tt:{setting}>{value}</tt:{setting}>"
        "</timg:ImagingSettings>"
        "<timg:ForcePersistence>false</timg:ForcePersistence>"
        "</timg:SetImagingSettings>"
    )
    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_IMAGING,
        prefix="timg",
        payload=payload,
        action="SetImagingSettings",
    )
    return {
        "operation": "onvif_set_imaging",
        "setting": setting,
        "value": value,
        "force_persistence": False,
        "readback_available": False,
        **_response_summary(response),
        "response": _xml_rows(root, limit=24),
    }


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


def _simple_items(section) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if section is None:
        return items
    for child in section.iter():
        if _local_name(child.tag) != "SimpleItem":
            continue
        name = str(child.attrib.get("Name") or "")[:120]
        value = str(child.attrib.get("Value") or "")[:240]
        if name:
            items.append({"name": name, "value": value})
            if len(items) >= 32:
                break
    return items


def _notification_messages(root) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if root is None:
        return messages
    for node in root.iter():
        if _local_name(node.tag) != "NotificationMessage":
            continue
        topic = None
        message_node = None
        for child in node.iter():
            local = _local_name(child.tag)
            if local == "Topic" and child.text:
                topic = child.text.strip()[:240]
            elif local == "Message" and child is not node:
                message_node = child

        source: list[dict[str, str]] = []
        data: list[dict[str, str]] = []
        key: list[dict[str, str]] = []
        utc_time = None
        property_operation = None
        if message_node is not None:
            utc_time = message_node.attrib.get("UtcTime")
            property_operation = message_node.attrib.get("PropertyOperation")
            for section in list(message_node):
                local = _local_name(section.tag)
                if local == "Source":
                    source = _simple_items(section)
                elif local == "Data":
                    data = _simple_items(section)
                elif local == "Key":
                    key = _simple_items(section)

        items = (source + key + data)[:32]
        motion = None
        for item in items:
            if item["name"].lower() in {"state", "ismotion", "motionactive"}:
                value = item["value"].strip().lower()
                if value in {"true", "1", "on", "active"}:
                    motion = True
                elif value in {"false", "0", "off", "inactive"}:
                    motion = False

        messages.append({
            "topic": topic,
            "utc_time": utc_time,
            "property_operation": property_operation,
            "source": source,
            "key": key,
            "data": data,
            "items": items,
            "motion": motion,
        })
        if len(messages) >= LAB_MAX_EVENT_MESSAGES:
            break
    return messages


def _event_signature(message: dict[str, Any]) -> tuple:
    def pairs(name: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (str(item.get("name") or ""), str(item.get("value") or ""))
            for item in (message.get(name) or [])
            if isinstance(item, dict)
        )

    return (
        str(message.get("topic") or ""),
        str(message.get("property_operation") or ""),
        pairs("source"),
        pairs("key"),
        pairs("data"),
        message.get("motion"),
    )


def _summarize_event_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    duplicate_count = 0
    initial_states: dict[str, bool] = {}
    latest_states: dict[str, bool] = {}
    state_changes: list[dict[str, Any]] = []

    for message in messages:
        signature = _event_signature(message)
        if signature in seen:
            duplicate_count += 1
        else:
            seen.add(signature)
            unique.append(message)

        topic = str(message.get("topic") or "")
        motion = message.get("motion")
        operation = str(message.get("property_operation") or "")
        if not topic or motion is None:
            continue
        motion = bool(motion)
        if operation.lower() == "initialized" and topic not in initial_states:
            initial_states[topic] = motion

        previous = latest_states.get(topic)
        if previous is not None and previous != motion:
            state_changes.append({
                "topic": topic,
                "from": previous,
                "to": motion,
                "utc_time": message.get("utc_time"),
                "property_operation": message.get("property_operation"),
                "pull_index": message.get("pull_index"),
            })
        latest_states[topic] = motion

    initialization_only = bool(messages) and all(
        str(message.get("property_operation") or "").lower() == "initialized"
        for message in messages
    )
    return {
        "unique_messages": unique,
        "unique_message_count": len(unique),
        "duplicate_message_count": duplicate_count,
        "initial_states": initial_states,
        "latest_states": latest_states,
        "state_changes": state_changes,
        "motion_transition_observed": bool(state_changes),
        "initialization_only": initialization_only,
    }


def onvif_pull_events(
    camera,
    onvif_info: dict,
    *,
    listen_seconds: int = 5,
) -> dict[str, Any]:
    """Create a bounded PullPoint subscription and distinguish snapshots from transitions."""
    listen_seconds = max(1, min(int(listen_seconds), LAB_MAX_PULL_SECONDS))
    events_path = _service_path(onvif_info, "events")
    lifetime = min(listen_seconds + 15, 45)
    create_response, create_root = _soap(
        camera,
        path=events_path,
        namespace=ONVIF_EVENTS,
        prefix="tev",
        payload=(
            "<tev:CreatePullPointSubscription>"
            f"<tev:InitialTerminationTime>PT{lifetime}S</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>"
        ),
        action="CreatePullPointSubscription",
        timeout=5.0,
    )
    create_summary = _response_summary(create_response)
    if not create_summary["accepted"]:
        return {
            "operation": "onvif_pull_events",
            "listen_seconds": listen_seconds,
            "subscription": create_summary,
            "messages": [],
        }

    pull_path = _subscription_path(create_root, events_path)
    messages: list[dict[str, Any]] = []
    pulls: list[dict[str, Any]] = []
    deadline = time.monotonic() + listen_seconds
    max_pulls = max(1, min(6, (listen_seconds + 4) // 5))

    for pull_index in range(max_pulls):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait_seconds = max(1, min(5, int(remaining + 0.999)))
        pull_response, pull_root = _soap(
            camera,
            path=pull_path,
            namespace=ONVIF_EVENTS,
            prefix="tev",
            payload=(
                "<tev:PullMessages>"
                f"<tev:Timeout>PT{wait_seconds}S</tev:Timeout>"
                "<tev:MessageLimit>16</tev:MessageLimit>"
                "</tev:PullMessages>"
            ),
            action="PullMessages",
            timeout=float(wait_seconds + 2),
        )
        summary = _response_summary(pull_response)
        pulls.append(summary)
        if not summary["accepted"]:
            break
        batch = _notification_messages(pull_root)
        for message in batch:
            message["pull_index"] = pull_index
        messages.extend(batch)
        if len(messages) >= LAB_MAX_EVENT_MESSAGES:
            messages = messages[:LAB_MAX_EVENT_MESSAGES]
            break

    event_summary = _summarize_event_messages(messages)
    return {
        "operation": "onvif_pull_events",
        "listen_seconds": listen_seconds,
        "subscription": create_summary,
        "pull": pulls[-1] if pulls else {},
        "pulls": pulls,
        "subscription_path": pull_path,
        "messages": messages,
        "message_count": len(messages),
        "motion_true_reported": any(
            item.get("motion") is True for item in messages
        ),
        **event_summary,
    }


def _recording_tokens(root) -> list[str]:
    tokens: list[str] = []
    if root is None:
        return tokens
    for node in root.iter():
        if _local_name(node.tag) != "RecordingToken" or not node.text:
            continue
        token = node.text.strip()
        if LAB_TOKEN_RE.fullmatch(token) and token not in tokens:
            tokens.append(token)
        if len(tokens) >= 16:
            break
    return tokens


def _replay_uri_descriptor(root) -> dict[str, Any] | None:
    if root is None:
        return None
    uri = None
    for node in root.iter():
        if _local_name(node.tag) == "Uri" and node.text:
            uri = node.text.strip()
            break
    if not uri:
        return None
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except (ValueError, TypeError):
        return {"valid_rtsp": False}
    return {
        "valid_rtsp": parsed.scheme.lower() == "rtsp",
        "path": parsed.path or "/",
        "reported_port": port,
        "reported_host_is_camera": parsed.hostname in {None, "", "127.0.0.1", "localhost"},
        "has_query": bool(parsed.query),
        "query_redacted": bool(parsed.query),
        "credentials_present": bool(parsed.username or parsed.password),
    }


def onvif_recording_playback_probe(
    camera,
    onvif_info: dict,
    *,
    recording_token: str | None = None,
) -> dict[str, Any]:
    """Inspect a camera-returned recording token and request, but do not open, its replay URI."""
    recording_path = _service_path_by_namespace(onvif_info, ONVIF_RECORDING)
    search_path = _service_path_by_namespace(onvif_info, ONVIF_SEARCH)
    replay_path = _service_path_by_namespace(onvif_info, ONVIF_REPLAY)
    if not recording_path or not search_path or not replay_path:
        raise LabError("Recording/Search/Replay services were not all advertised")

    recordings_response, recordings_root = _soap(
        camera,
        path=recording_path,
        namespace=ONVIF_RECORDING,
        prefix="trc",
        payload="<trc:GetRecordings/>",
        action="GetRecordings",
        timeout=5.0,
    )
    recordings_summary = _response_summary(recordings_response)
    tokens = _recording_tokens(recordings_root) if recordings_summary["accepted"] else []
    if not tokens:
        return {
            "operation": "onvif_recording_playback_probe",
            "recordings": recordings_summary,
            "recording_tokens": [],
            "error": "Camera returned no recording token",
        }

    if recording_token:
        token = _safe_token(recording_token, label="recording token")
        if token not in tokens:
            raise LabError("Recording token was not returned by the camera")
    else:
        token = tokens[0]

    info_response, info_root = _soap(
        camera,
        path=search_path,
        namespace=ONVIF_SEARCH,
        prefix="tse",
        payload=(
            "<tse:GetRecordingInformation>"
            f"<tse:RecordingToken>{xml_escape(token)}</tse:RecordingToken>"
            "</tse:GetRecordingInformation>"
        ),
        action="GetRecordingInformation",
        timeout=5.0,
    )

    replay_response, replay_root = _soap(
        camera,
        path=replay_path,
        namespace=ONVIF_REPLAY,
        prefix="trp",
        payload=(
            "<trp:GetReplayUri>"
            "<trp:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            "</trp:StreamSetup>"
            f"<trp:RecordingToken>{xml_escape(token)}</trp:RecordingToken>"
            "</trp:GetReplayUri>"
        ),
        action="GetReplayUri",
        timeout=5.0,
    )

    return {
        "operation": "onvif_recording_playback_probe",
        "recording_token": token,
        "recording_tokens": tokens,
        "recordings": recordings_summary,
        "recording_information": {
            **_response_summary(info_response),
            "values": _xml_rows(info_root),
            "hierarchy": _xml_tree(info_root),
        },
        "replay_uri": {
            **_response_summary(replay_response),
            "descriptor": _replay_uri_descriptor(replay_root),
            "raw_uri_exposed": False,
        },
    }


def _recording_jobs(root) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if root is None:
        return jobs
    for node in root.iter():
        if _local_name(node.tag) != "JobItem":
            continue
        token = None
        mode = None
        recording_token = None
        priority = None
        for child in node.iter():
            local = _local_name(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            if local == "JobToken" and token is None:
                token = text
            elif local == "Mode" and mode is None:
                mode = text
            elif local == "RecordingToken" and recording_token is None:
                recording_token = text
            elif local == "Priority" and priority is None:
                priority = text
        if token and LAB_TOKEN_RE.fullmatch(token):
            jobs.append({
                "token": token,
                "mode": mode,
                "recording_token": recording_token,
                "priority": priority,
            })
        if len(jobs) >= 8:
            break
    return jobs


def _recording_job_read(
    camera,
    *,
    path: str,
    token: str,
    action: str,
) -> dict[str, Any]:
    token = _safe_token(token, label="recording job token")
    if action == "GetRecordingJobConfiguration":
        payload = (
            "<trc:GetRecordingJobConfiguration>"
            f"<trc:JobToken>{xml_escape(token)}</trc:JobToken>"
            "</trc:GetRecordingJobConfiguration>"
        )
    elif action == "GetRecordingJobState":
        payload = (
            "<trc:GetRecordingJobState>"
            f"<trc:JobToken>{xml_escape(token)}</trc:JobToken>"
            "</trc:GetRecordingJobState>"
        )
    else:
        raise LabError("Unsupported recording job read operation")

    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_RECORDING,
        prefix="trc",
        payload=payload,
        action=action,
        timeout=5.0,
    )
    return {
        **_response_summary(response),
        "values": _xml_rows(root),
        "hierarchy": _xml_tree(root),
    }


def onvif_recording_job_discovery(camera, onvif_info: dict) -> dict[str, Any]:
    """Read existing recording jobs and their configuration/state."""
    path = _service_path_by_namespace(onvif_info, ONVIF_RECORDING)
    if not path:
        raise LabError("Recording service was not advertised")

    response, root = _soap(
        camera,
        path=path,
        namespace=ONVIF_RECORDING,
        prefix="trc",
        payload="<trc:GetRecordingJobs/>",
        action="GetRecordingJobs",
        timeout=5.0,
    )
    summary = _response_summary(response)
    jobs = _recording_jobs(root) if summary["accepted"] else []
    details = []
    for job in jobs[:4]:
        token = job["token"]
        details.append({
            **job,
            "configuration": _recording_job_read(
                camera,
                path=path,
                token=token,
                action="GetRecordingJobConfiguration",
            ),
            "state": _recording_job_read(
                camera,
                path=path,
                token=token,
                action="GetRecordingJobState",
            ),
        })

    return {
        "operation": "onvif_recording_job_discovery",
        "path": path,
        "jobs": jobs,
        "job_count": len(jobs),
        "details": details,
        "get_jobs": summary,
    }


def _recording_info_by_token(
    camera,
    onvif_info: dict,
    recording_token: str,
) -> dict[str, Any]:
    search_path = _service_path_by_namespace(onvif_info, ONVIF_SEARCH)
    if not search_path:
        return {"accepted": False, "error": "Search service was not advertised"}
    token = _safe_token(recording_token, label="recording token")
    response, root = _soap(
        camera,
        path=search_path,
        namespace=ONVIF_SEARCH,
        prefix="tse",
        payload=(
            "<tse:GetRecordingInformation>"
            f"<tse:RecordingToken>{xml_escape(token)}</tse:RecordingToken>"
            "</tse:GetRecordingInformation>"
        ),
        action="GetRecordingInformation",
        timeout=5.0,
    )
    return {
        **_response_summary(response),
        "values": _xml_rows(root),
        "hierarchy": _xml_tree(root),
    }


def onvif_recording_pulse(
    camera,
    onvif_info: dict,
    *,
    seconds: int = 5,
) -> dict[str, Any]:
    """Activate one existing idle recording job briefly, then restore Idle.

    This never creates/deletes recordings, tracks or jobs and never edits job
    configuration. A write is attempted only when exactly one existing job is
    returned and its current mode is explicitly Idle.
    """
    seconds = max(1, min(int(seconds), 5))
    discovery = onvif_recording_job_discovery(camera, onvif_info)
    jobs = discovery.get("jobs") or []
    if len(jobs) != 1:
        return {
            "operation": "onvif_recording_pulse",
            "seconds": seconds,
            "executed": False,
            "reason": "Exactly one existing recording job is required",
            "discovery": discovery,
        }

    job = jobs[0]
    job_token = _safe_token(job.get("token"), label="recording job token")
    mode = str(job.get("mode") or "")
    recording_token = str(job.get("recording_token") or "")
    if mode != "Idle":
        return {
            "operation": "onvif_recording_pulse",
            "seconds": seconds,
            "executed": False,
            "reason": f"Recording job mode is {mode or 'unknown'}, not Idle",
            "job": job,
            "discovery": discovery,
        }
    if not LAB_TOKEN_RE.fullmatch(recording_token):
        return {
            "operation": "onvif_recording_pulse",
            "seconds": seconds,
            "executed": False,
            "reason": "Recording job did not expose a safe recording token",
            "job": job,
            "discovery": discovery,
        }

    path = _service_path_by_namespace(onvif_info, ONVIF_RECORDING)
    before_info = _recording_info_by_token(camera, onvif_info, recording_token)

    active_response = None
    active_state = None
    active_info = None
    restore_response = None
    restored_state = None
    after_info = None
    try:
        active_response, _ = _soap(
            camera,
            path=path,
            namespace=ONVIF_RECORDING,
            prefix="trc",
            payload=(
                "<trc:SetRecordingJobMode>"
                f"<trc:JobToken>{xml_escape(job_token)}</trc:JobToken>"
                "<trc:Mode>Active</trc:Mode>"
                "</trc:SetRecordingJobMode>"
            ),
            action="SetRecordingJobMode",
            timeout=5.0,
        )
        if _response_summary(active_response)["accepted"]:
            active_state = _recording_job_read(
                camera,
                path=path,
                token=job_token,
                action="GetRecordingJobState",
            )
            active_info = _recording_info_by_token(camera, onvif_info, recording_token)
            time.sleep(seconds)
    finally:
        if active_response is not None and _response_summary(active_response)["accepted"]:
            restore_response, _ = _soap(
                camera,
                path=path,
                namespace=ONVIF_RECORDING,
                prefix="trc",
                payload=(
                    "<trc:SetRecordingJobMode>"
                    f"<trc:JobToken>{xml_escape(job_token)}</trc:JobToken>"
                    "<trc:Mode>Idle</trc:Mode>"
                    "</trc:SetRecordingJobMode>"
                ),
                action="SetRecordingJobMode",
                timeout=5.0,
            )
            restored_state = _recording_job_read(
                camera,
                path=path,
                token=job_token,
                action="GetRecordingJobState",
            )
            after_info = _recording_info_by_token(camera, onvif_info, recording_token)

    return {
        "operation": "onvif_recording_pulse",
        "seconds": seconds,
        "executed": bool(active_response and _response_summary(active_response)["accepted"]),
        "job": job,
        "before_recording_information": before_info,
        "activate": _response_summary(active_response or {}),
        "active_state": active_state,
        "active_recording_information": active_info,
        "restore_idle": _response_summary(restore_response or {}),
        "restored_state": restored_state,
        "after_recording_information": after_info,
        "created_objects": False,
        "deleted_objects": False,
        "job_configuration_changed": False,
    }


def _read_service_operation(
    camera,
    *,
    path: str,
    namespace: str,
    prefix: str,
    action: str,
    payload: str,
) -> dict[str, Any]:
    response, root = _soap(
        camera,
        path=path,
        namespace=namespace,
        prefix=prefix,
        payload=payload,
        action=action,
        timeout=5.0,
    )
    return {
        **_response_summary(response),
        "values": _xml_rows(root),
        "hierarchy": _xml_tree(root),
    }


def onvif_storage_discovery(camera, onvif_info: dict) -> dict[str, Any]:
    """Probe only read-only Recording/Search/Replay operations advertised by GetServices."""
    specs = (
        (
            "recording",
            ONVIF_RECORDING,
            "trc",
            (
                ("service_capabilities", "GetServiceCapabilities", "<trc:GetServiceCapabilities/>"),
                ("recordings", "GetRecordings", "<trc:GetRecordings/>"),
            ),
        ),
        (
            "search",
            ONVIF_SEARCH,
            "tse",
            (
                ("service_capabilities", "GetServiceCapabilities", "<tse:GetServiceCapabilities/>"),
                ("recording_summary", "GetRecordingSummary", "<tse:GetRecordingSummary/>"),
            ),
        ),
        (
            "replay",
            ONVIF_REPLAY,
            "trp",
            (
                ("service_capabilities", "GetServiceCapabilities", "<trp:GetServiceCapabilities/>"),
                ("configuration", "GetReplayConfiguration", "<trp:GetReplayConfiguration/>"),
            ),
        ),
    )
    result: dict[str, Any] = {
        "operation": "onvif_storage_discovery",
        "advertised_services": _services_list(onvif_info),
    }

    for key, namespace, prefix, operations in specs:
        path = _service_path_by_namespace(onvif_info, namespace)
        entry: dict[str, Any] = {
            "advertised": bool(path),
            "path": path,
            "namespace": namespace,
        }
        if path:
            for op_key, action, payload in operations:
                entry[op_key] = _read_service_operation(
                    camera,
                    path=path,
                    namespace=namespace,
                    prefix=prefix,
                    action=action,
                    payload=payload,
                )
                status = entry[op_key].get("http")
                if status is None or status == 401:
                    break
        result[key] = entry
    return result

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
