from __future__ import annotations

import http.client
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml_escape

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
ONVIF_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
ONVIF_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
ONVIF_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
ONVIF_SCHEMA = "http://www.onvif.org/ver10/schema"
ONVIF_REQUEST_GAP = 0.15
MAX_ONVIF_PROFILES = 4

def icmp_probe(host: str, timeout: float = 1.5) -> dict[str, Any]:
    """Check liveness without opening a camera service TCP socket.

    The tested JA-A12 can become unresponsive when clients open TCP
    connections and close them without speaking the expected protocol.
    ICMP avoids consuming HTTP/RTSP/ONVIF server connection slots.
    """
    seconds = max(1, int(round(timeout)))
    try:
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", str(seconds), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=max(timeout + 1.0, 2.0),
            check=False,
        )
        if proc.returncode == 0:
            return {"online": True, "method": "icmp", "error": None}

        stderr = proc.stderr.decode("utf-8", "replace").strip()
        lowered = stderr.lower()
        if any(
            marker in lowered
            for marker in ("operation not permitted", "permission denied", "not found")
        ):
            return {
                "online": None,
                "method": "icmp",
                "error": "ICMP heartbeat is unavailable in this container",
            }
        return {"online": False, "method": "icmp", "error": "ICMP ping failed"}
    except subprocess.TimeoutExpired:
        return {"online": False, "method": "icmp", "error": "ICMP ping timeout"}
    except OSError:
        return {
            "online": None,
            "method": "icmp",
            "error": "ICMP heartbeat is unavailable in this container",
        }

def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def _safe_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

def _safe_service_path(xaddr: str | None, fallback: str | None = None) -> str | None:
    """Return only the path component from an ONVIF XAddr.

    The camera controls the XAddr contents, so the App never follows the
    hostname reported by the device. All SOAP calls remain pinned to the
    configured private camera IP and configured ONVIF port.
    """
    if not xaddr:
        return fallback
    try:
        parsed = urlsplit(xaddr)
    except ValueError:
        return fallback
    path = parsed.path or fallback
    if not path or not path.startswith("/") or ".." in path:
        return fallback
    return path

def _soap_envelope(namespace: str, prefix: str, payload: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_ENV}" xmlns:{prefix}="{namespace}" '
        f'xmlns:tt="{ONVIF_SCHEMA}">'
        f"<s:Body>{payload}</s:Body></s:Envelope>"
    ).encode("utf-8")

def _soap_post(
    host: str,
    port: int,
    path: str,
    body: bytes,
    *,
    action: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    if not path.startswith("/") or ".." in path:
        return {"status": None, "body": b"", "error": "unsafe ONVIF service path"}

    headers = {
        "Content-Type": "application/soap+xml; charset=utf-8"
        + (f'; action="{action}"' if action else ""),
        "Content-Length": str(len(body)),
        "Connection": "close",
        "User-Agent": "JOOAN-Local-Control",
    }

    connection = http.client.HTTPConnection(host, int(port), timeout=timeout)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(262144)
        return {
            "status": response.status,
            "body": payload,
            "error": None,
            "authentication_required": response.status == 401,
        }
    except (OSError, http.client.HTTPException) as exc:
        return {
            "status": None,
            "body": b"",
            "error": str(exc),
            "authentication_required": None,
        }
    finally:
        connection.close()
        # Give the embedded ONVIF service a brief recovery window before the
        # next SOAP transaction. This is intentionally small but prevents a
        # deep diagnostic from becoming a tight request burst.
        time.sleep(ONVIF_REQUEST_GAP)

def _parse_xml(body: bytes) -> ET.Element | None:
    if not body:
        return None
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        return None

def _soap_fault(root: ET.Element | None) -> str | None:
    if root is None:
        return None
    fault = next((item for item in root.iter() if _local_name(item.tag) == "Fault"), None)
    if fault is None:
        return None
    for element in fault.iter():
        if _local_name(element.tag) in {"Text", "Reason"} and element.text:
            value = element.text.strip()
            if value:
                return value[:240]
    return "ONVIF SOAP fault"

def _extract_capability_services(root: ET.Element | None) -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    if root is None:
        return services

    capabilities = next(
        (item for item in root.iter() if _local_name(item.tag) == "Capabilities"),
        None,
    )
    if capabilities is None:
        return services

    for category in list(capabilities):
        name = _local_name(category.tag).lower()
        xaddr = None
        for item in category.iter():
            if _local_name(item.tag) == "XAddr" and item.text:
                xaddr = item.text.strip()
                break
        if not xaddr:
            continue
        try:
            parsed = urlsplit(xaddr)
            reported_port = parsed.port
        except ValueError:
            continue
        services[name] = {
            "path": _safe_service_path(xaddr),
            "reported_scheme": parsed.scheme or None,
            "reported_port": reported_port,
        }
    return services

def _extract_profiles(root: ET.Element | None) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if root is None:
        return profiles

    for node in root.iter():
        if _local_name(node.tag) != "Profiles":
            continue
        token = node.attrib.get("token")
        item: dict[str, Any] = {"token": token, "name": None, "video": {}, "audio": {}}

        for child in node:
            name = _local_name(child.tag)
            if name == "Name" and child.text:
                item["name"] = child.text.strip()
            elif name == "VideoEncoderConfiguration":
                for value in child.iter():
                    local = _local_name(value.tag)
                    if local == "Encoding" and value.text:
                        item["video"]["encoding"] = value.text.strip()
                    elif local == "Width" and value.text:
                        parsed = _safe_int(value.text)
                        if parsed is not None:
                            item["video"]["width"] = parsed
                    elif local == "Height" and value.text:
                        parsed = _safe_int(value.text)
                        if parsed is not None:
                            item["video"]["height"] = parsed
                    elif local == "FrameRateLimit" and value.text:
                        parsed = _safe_int(value.text)
                        if parsed is not None:
                            item["video"]["frame_rate_limit"] = parsed
                    elif local == "BitrateLimit" and value.text:
                        parsed = _safe_int(value.text)
                        if parsed is not None:
                            item["video"]["bitrate_limit_kbps"] = parsed
            elif name == "AudioEncoderConfiguration":
                for value in child.iter():
                    local = _local_name(value.tag)
                    if local == "Encoding" and value.text:
                        item["audio"]["encoding"] = value.text.strip()
                    elif local == "Bitrate" and value.text:
                        parsed = _safe_int(value.text)
                        if parsed is not None:
                            item["audio"]["bitrate_kbps"] = parsed
                    elif local == "SampleRate" and value.text:
                        parsed = _safe_int(value.text)
                        if parsed is not None:
                            item["audio"]["sample_rate_khz"] = parsed

        profiles.append(item)
        if len(profiles) >= MAX_ONVIF_PROFILES:
            break
    return profiles

def onvif_ptz_discovery(
    host: str,
    port: int = 8899,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Discover only the ONVIF pieces required by the production PTZ control.

    The full diagnostic probe is intentionally avoided here. This performs at
    most GetCapabilities + GetProfiles and does not open RTSP, query presets,
    enumerate nodes/configurations, or fan out into device diagnostics.
    """
    result: dict[str, Any] = {
        "port": int(port),
        "reachable": False,
        "http_detected": False,
        "path": None,
        "status": None,
        "authentication_required": None,
        "services": {},
        "profiles": [],
        "ptz": {},
        "error": None,
        "probe_mode": "ptz-minimal",
    }

    device_request = _soap_envelope(
        ONVIF_DEVICE,
        "tds",
        "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>",
    )

    device_response = None
    for path in ("/onvif/device_service", "/onvif/Device", "/onvif/device"):
        response = _soap_post(
            host,
            port,
            path,
            device_request,
            action=f"{ONVIF_DEVICE}/GetCapabilities",
            timeout=timeout,
        )
        if response["status"] is None:
            result["error"] = response["error"]
            return result

        result.update({
            "http_detected": True,
            "path": path,
            "status": response["status"],
            "authentication_required": response["authentication_required"],
            "error": response["error"],
        })
        device_response = response
        result["reachable"] = True
        if response["status"] in {200, 401}:
            break
        if response["status"] != 404:
            break

    if not device_response or device_response["status"] != 200:
        return result

    root = _parse_xml(device_response["body"])
    if root is None:
        result["error"] = "ONVIF GetCapabilities returned non-XML data"
        return result
    fault = _soap_fault(root)
    if fault:
        result["error"] = f"ONVIF fault: {fault}"
        return result

    services = _extract_capability_services(root)
    result["services"] = services
    media_path = (services.get("media") or {}).get("path")
    ptz_path = (services.get("ptz") or {}).get("path")

    if not media_path:
        result["error"] = "ONVIF media service was not advertised"
        return result

    profiles_response = _soap_post(
        host,
        port,
        media_path,
        _soap_envelope(ONVIF_MEDIA, "trt", "<trt:GetProfiles/>"),
        action=f"{ONVIF_MEDIA}/GetProfiles",
        timeout=timeout,
    )
    result["media_status"] = profiles_response["status"]
    result["media_authentication_required"] = profiles_response["authentication_required"]
    if profiles_response["status"] != 200:
        result["error"] = (
            profiles_response["error"]
            or (
                "ONVIF media authentication required"
                if profiles_response["status"] == 401
                else f"ONVIF GetProfiles returned HTTP {profiles_response['status']}"
            )
        )
        return result

    profiles_root = _parse_xml(profiles_response["body"])
    profiles = _extract_profiles(profiles_root)
    result["profiles"] = profiles

    token = profiles[0].get("token") if profiles else None
    if ptz_path and token:
        result["ptz"] = {
            "path": ptz_path,
            "profile_token": token,
        }

    return result

def _onvif_response_summary(response: dict[str, Any]) -> dict[str, Any]:
    root = _parse_xml(response.get("body") or b"")
    fault = _soap_fault(root)
    return {
        "http": response.get("status"),
        "authentication_required": response.get("authentication_required"),
        "accepted": response.get("status") == 200 and fault is None,
        "fault": fault,
        "error": response.get("error"),
    }

def onvif_continuous_move(
    host: str,
    port: int,
    onvif_info: dict[str, Any],
    *,
    direction: str,
    speed: float = 0.25,
    duration_ms: int = 250,
) -> dict[str, Any]:
    """Run one bounded ONVIF PTZ pulse using previously discovered service data."""
    if direction not in {"up", "down", "left", "right"}:
        raise ValueError("Unsupported ONVIF PTZ direction")

    speed = max(0.1, min(float(speed), 1.0))
    duration_ms = max(80, min(int(duration_ms), 800))
    ptz = onvif_info.get("ptz") or {}
    path = _safe_service_path(
        ptz.get("path")
        or ((onvif_info.get("services") or {}).get("ptz") or {}).get("path")
    )
    token = str(ptz.get("profile_token") or "").strip()
    if not path:
        raise ValueError("ONVIF PTZ service was not discovered")
    if not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,128}", token):
        raise ValueError("Invalid ONVIF PTZ profile token")

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

    start_response: dict[str, Any] | None = None
    stop_response: dict[str, Any] | None = None
    try:
        start_response = _soap_post(
            host,
            port,
            path,
            _soap_envelope(ONVIF_PTZ, "tptz", start_payload),
            action=f"{ONVIF_PTZ}/ContinuousMove",
            timeout=4.0,
        )
        if start_response.get("status") == 200:
            time.sleep(duration_ms / 1000.0)
    finally:
        stop_response = _soap_post(
            host,
            port,
            path,
            _soap_envelope(ONVIF_PTZ, "tptz", stop_payload),
            action=f"{ONVIF_PTZ}/Stop",
            timeout=4.0,
        )

    return {
        "operation": "onvif_continuous_move",
        "direction": direction,
        "speed": speed,
        "duration_ms": duration_ms,
        "start": _onvif_response_summary(start_response or {}),
        "stop": _onvif_response_summary(stop_response or {}),
    }

def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )

def capture_snapshot(url: str, timeout: float = 10.0) -> bytes:
    try:
        proc = _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                url,
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ],
            timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("snapshot timeout") from exc

    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("could not capture RTSP snapshot")
    return proc.stdout
