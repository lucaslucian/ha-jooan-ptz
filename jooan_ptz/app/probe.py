from __future__ import annotations

import http.client
import json
import logging
import socket
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("jooan_ptz.probe")

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
ONVIF_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
ONVIF_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
ONVIF_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
ONVIF_SCHEMA = "http://www.onvif.org/ver10/schema"


@dataclass(slots=True)
class PortProbe:
    port: int
    reachable: bool
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"port": self.port, "reachable": self.reachable, "error": self.error}


def tcp_probe(host: str, port: int, timeout: float = 1.5) -> PortProbe:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return PortProbe(int(port), True)
    except OSError as exc:
        return PortProbe(int(port), False, str(exc))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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


def _safe_rtsp_descriptor(uri: str | None) -> dict[str, Any] | None:
    """Return non-secret RTSP metadata from an ONVIF GetStreamUri result."""
    if not uri:
        return None
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None
    if parsed.scheme.lower() != "rtsp":
        return None
    path = parsed.path or "/"
    if not path.startswith("/") or ".." in path:
        return None
    return {
        "path": path,
        "reported_port": parsed.port,
        "reported_host_is_loopback": parsed.hostname in {"127.0.0.1", "::1", "localhost"},
    }


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
    for element in root.iter():
        if _local_name(element.tag) in {"Text", "Reason"} and element.text:
            # Return a short diagnostic only. Do not echo arbitrary XML bodies.
            value = element.text.strip()
            if value:
                return value[:240]
    return None


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
        services[name] = {
            "path": _safe_service_path(xaddr),
            "reported_scheme": urlsplit(xaddr).scheme or None,
            "reported_port": urlsplit(xaddr).port,
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
                        item["video"]["width"] = int(value.text)
                    elif local == "Height" and value.text:
                        item["video"]["height"] = int(value.text)
                    elif local == "FrameRateLimit" and value.text:
                        item["video"]["frame_rate_limit"] = int(value.text)
                    elif local == "BitrateLimit" and value.text:
                        item["video"]["bitrate_limit_kbps"] = int(value.text)
            elif name == "AudioEncoderConfiguration":
                for value in child.iter():
                    local = _local_name(value.tag)
                    if local == "Encoding" and value.text:
                        item["audio"]["encoding"] = value.text.strip()
                    elif local == "Bitrate" and value.text:
                        item["audio"]["bitrate_kbps"] = int(value.text)
                    elif local == "SampleRate" and value.text:
                        item["audio"]["sample_rate_khz"] = int(value.text)

        profiles.append(item)
    return profiles


def _extract_stream_uri(root: ET.Element | None) -> dict[str, Any] | None:
    if root is None:
        return None
    for node in root.iter():
        if _local_name(node.tag) == "Uri" and node.text:
            return _safe_rtsp_descriptor(node.text.strip())
    return None


def _extract_ptz_status(root: ET.Element | None) -> dict[str, Any]:
    status: dict[str, Any] = {}
    if root is None:
        return status
    for node in root.iter():
        name = _local_name(node.tag)
        if name == "PanTilt":
            if "x" in node.attrib:
                status["pan"] = node.attrib.get("x")
            if "y" in node.attrib:
                status["tilt"] = node.attrib.get("y")
        elif name == "Zoom" and "x" in node.attrib:
            status["zoom"] = node.attrib.get("x")
        elif name == "MoveStatus":
            for child in node:
                status[f"move_{_local_name(child.tag).lower()}"] = (child.text or "").strip()
        elif name in {"UtcTime", "PositionError"} and node.text:
            status[name.lower()] = node.text.strip()
    return status


def _extract_presets(root: ET.Element | None) -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    if root is None:
        return presets
    for node in root.iter():
        if _local_name(node.tag) != "Preset":
            continue
        item = {"token": node.attrib.get("token"), "name": None}
        for child in node:
            if _local_name(child.tag) == "Name" and child.text:
                item["name"] = child.text.strip()
        presets.append(item)
    return presets


def onvif_probe(host: str, port: int = 8899, timeout: float = 3.0) -> dict[str, Any]:
    """Perform read-only ONVIF discovery against the configured LAN camera."""
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
    }
    port_status = tcp_probe(host, port, timeout=min(timeout, 1.5))
    result["reachable"] = port_status.reachable
    if not port_status.reachable:
        result["error"] = port_status.error
        return result

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
            continue

        result.update(
            {
                "http_detected": True,
                "path": path,
                "status": response["status"],
                "authentication_required": response["authentication_required"],
                "error": response["error"],
            }
        )
        device_response = response
        if response["status"] == 200:
            break

    if not device_response or device_response["status"] != 200:
        return result

    device_root = _parse_xml(device_response["body"])
    if device_root is None:
        result["error"] = "ONVIF GetCapabilities returned non-XML data"
        return result

    fault = _soap_fault(device_root)
    if fault:
        result["error"] = f"ONVIF fault: {fault}"
        return result

    services = _extract_capability_services(device_root)
    result["services"] = services

    media_path = (services.get("media") or {}).get("path")
    if media_path:
        profiles_request = _soap_envelope(
            ONVIF_MEDIA,
            "trt",
            "<trt:GetProfiles/>",
        )
        profiles_response = _soap_post(
            host,
            port,
            media_path,
            profiles_request,
            action=f"{ONVIF_MEDIA}/GetProfiles",
            timeout=timeout,
        )
        result["media_status"] = profiles_response["status"]
        result["media_authentication_required"] = profiles_response["authentication_required"]

        if profiles_response["status"] == 200:
            profiles_root = _parse_xml(profiles_response["body"])
            profiles = _extract_profiles(profiles_root)

            for profile in profiles:
                token = profile.get("token")
                if not token:
                    continue
                stream_request = _soap_envelope(
                    ONVIF_MEDIA,
                    "trt",
                    (
                        "<trt:GetStreamUri>"
                        "<trt:StreamSetup>"
                        "<tt:Stream>RTP-Unicast</tt:Stream>"
                        "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
                        "</trt:StreamSetup>"
                        f"<trt:ProfileToken>{token}</trt:ProfileToken>"
                        "</trt:GetStreamUri>"
                    ),
                )
                stream_response = _soap_post(
                    host,
                    port,
                    media_path,
                    stream_request,
                    action=f"{ONVIF_MEDIA}/GetStreamUri",
                    timeout=timeout,
                )
                profile["stream_status"] = stream_response["status"]
                if stream_response["status"] == 200:
                    profile["stream"] = _extract_stream_uri(
                        _parse_xml(stream_response["body"])
                    )
                else:
                    profile["stream"] = None

            result["profiles"] = profiles
        elif profiles_response["error"]:
            result["media_error"] = profiles_response["error"]

    ptz_path = (services.get("ptz") or {}).get("path")
    profiles = result.get("profiles") or []
    if ptz_path and profiles:
        token = profiles[0].get("token")
        if token:
            ptz: dict[str, Any] = {"path": ptz_path, "profile_token": token}

            status_request = _soap_envelope(
                ONVIF_PTZ,
                "tptz",
                (
                    "<tptz:GetStatus>"
                    f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
                    "</tptz:GetStatus>"
                ),
            )
            status_response = _soap_post(
                host,
                port,
                ptz_path,
                status_request,
                action=f"{ONVIF_PTZ}/GetStatus",
                timeout=timeout,
            )
            ptz["status_http"] = status_response["status"]
            ptz["status"] = (
                _extract_ptz_status(_parse_xml(status_response["body"]))
                if status_response["status"] == 200
                else {}
            )

            presets_request = _soap_envelope(
                ONVIF_PTZ,
                "tptz",
                (
                    "<tptz:GetPresets>"
                    f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
                    "</tptz:GetPresets>"
                ),
            )
            presets_response = _soap_post(
                host,
                port,
                ptz_path,
                presets_request,
                action=f"{ONVIF_PTZ}/GetPresets",
                timeout=timeout,
            )
            ptz["presets_http"] = presets_response["status"]
            ptz["presets"] = (
                _extract_presets(_parse_xml(presets_response["body"]))
                if presets_response["status"] == 200
                else []
            )
            result["ptz"] = ptz

    return result


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def ffprobe_rtsp(url: str, timeout: float = 8.0) -> dict[str, Any]:
    """Probe one RTSP URL without returning credentials."""
    try:
        proc = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-rtsp_transport",
                "tcp",
                "-show_entries",
                "stream=index,codec_type,codec_name,width,height,sample_rate,channels",
                "-of",
                "json",
                url,
            ],
            timeout,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "ffprobe timeout", "streams": []}
    except OSError as exc:
        return {"available": False, "error": f"ffprobe unavailable: {exc}", "streams": []}

    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        host = urlsplit(url).hostname
        if host:
            message = message.replace(url, f"rtsp://{host}/<redacted>")
        return {"available": False, "error": message[-500:], "streams": []}

    try:
        payload = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {"available": False, "error": "invalid ffprobe JSON", "streams": []}

    streams = []
    for item in payload.get("streams", []):
        if not isinstance(item, dict):
            continue
        streams.append(
            {
                key: item.get(key)
                for key in (
                    "index",
                    "codec_type",
                    "codec_name",
                    "width",
                    "height",
                    "sample_rate",
                    "channels",
                )
                if item.get(key) is not None
            }
        )
    return {"available": bool(streams), "error": None, "streams": streams}


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
