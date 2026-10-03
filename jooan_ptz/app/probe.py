from __future__ import annotations

import http.client
import json
import logging
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from typing import Any
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("jooan_ptz.probe")

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
ONVIF_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
ONVIF_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
ONVIF_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
ONVIF_SCHEMA = "http://www.onvif.org/ver10/schema"
ONVIF_REQUEST_GAP = 0.15
MAX_ONVIF_PROFILES = 4
MAX_ONVIF_LIST_ITEMS = 32
MAX_ONVIF_PRESETS = 64


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


def _redact_process_output(value: str) -> str:
    return re.sub(
        r"(rtsp://[^:/@\s]+:)[^@\s]+(@)",
        r"\1<redacted>\2",
        value,
        flags=re.I,
    )


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
    try:
        reported_port = parsed.port
    except ValueError:
        reported_port = None
    return {
        "path": path,
        "reported_port": reported_port,
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
        if len(presets) >= MAX_ONVIF_PRESETS:
            break
    return presets


def _first_text(root: ET.Element | None, name: str) -> str | None:
    if root is None:
        return None
    for node in root.iter():
        if _local_name(node.tag) == name and node.text:
            return node.text.strip()
    return None


def _extract_device_information(root: ET.Element | None) -> dict[str, Any]:
    mapping = {
        "Manufacturer": "manufacturer",
        "Model": "model",
        "FirmwareVersion": "firmware_version",
        "SerialNumber": "serial_number",
        "HardwareId": "hardware_id",
    }
    return {
        target: value
        for source, target in mapping.items()
        if (value := _first_text(root, source)) is not None
    }


def _extract_system_datetime(root: ET.Element | None) -> dict[str, Any]:
    if root is None:
        return {}
    result: dict[str, Any] = {}
    for key in ("DateTimeType", "DaylightSavings"):
        value = _first_text(root, key)
        if value is not None:
            result[key.lower()] = value
    tz = _first_text(root, "TZ")
    if tz:
        result["timezone"] = tz
    utc = next((n for n in root.iter() if _local_name(n.tag) == "UTCDateTime"), None)
    if utc is not None:
        parts = {}
        for node in utc.iter():
            name = _local_name(node.tag)
            if name in {"Year","Month","Day","Hour","Minute","Second"} and node.text:
                parts[name.lower()] = node.text.strip()
        result["utc"] = parts
    return result


def _extract_scopes(root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    values = []
    for node in root.iter():
        if _local_name(node.tag) == "ScopeItem" and node.text:
            values.append(node.text.strip())
    return values[:64]


def _extract_network_interfaces(root: ET.Element | None) -> list[dict[str, Any]]:
    interfaces = []
    if root is None:
        return interfaces
    for node in root.iter():
        if _local_name(node.tag) != "NetworkInterfaces":
            continue
        item: dict[str, Any] = {
            "token": node.attrib.get("token"),
            "enabled": _first_text(node, "Enabled"),
            "name": _first_text(node, "Name"),
            "hw_address": _first_text(node, "HwAddress"),
            "mtu": _first_text(node, "MTU"),
            "ipv4": [],
            "ipv6": [],
        }
        for child in node.iter():
            local = _local_name(child.tag)
            if local == "Manual":
                address = _first_text(child, "Address")
                prefix = _first_text(child, "PrefixLength")
                if address:
                    target = item["ipv6"] if ":" in address else item["ipv4"]
                    target.append({"address": address, "prefix_length": prefix})
        interfaces.append(item)
        if len(interfaces) >= MAX_ONVIF_LIST_ITEMS:
            break
    return interfaces


def _extract_services_list(root: ET.Element | None) -> list[dict[str, Any]]:
    services = []
    if root is None:
        return services
    for node in root.iter():
        if _local_name(node.tag) != "Service":
            continue
        namespace = _first_text(node, "Namespace")
        xaddr = _first_text(node, "XAddr")
        major = _first_text(node, "Major")
        minor = _first_text(node, "Minor")
        services.append({
            "namespace": namespace,
            "path": _safe_service_path(xaddr),
            "version": f"{major}.{minor}" if major is not None and minor is not None else None,
        })
        if len(services) >= MAX_ONVIF_LIST_ITEMS:
            break
    return services


def _extract_video_sources(root: ET.Element | None) -> list[dict[str, Any]]:
    sources = []
    if root is None:
        return sources
    for node in root.iter():
        if _local_name(node.tag) != "VideoSources":
            continue
        item = {
            "token": node.attrib.get("token"),
            "framerate": _first_text(node, "Framerate"),
            "width": _first_text(node, "Width"),
            "height": _first_text(node, "Height"),
        }
        sources.append(item)
        if len(sources) >= MAX_ONVIF_LIST_ITEMS:
            break
    return sources


def _extract_audio_sources(root: ET.Element | None) -> list[dict[str, Any]]:
    sources = []
    if root is None:
        return sources
    for node in root.iter():
        if _local_name(node.tag) != "AudioSources":
            continue
        sources.append({
            "token": node.attrib.get("token"),
            "channels": _first_text(node, "Channels"),
        })
        if len(sources) >= MAX_ONVIF_LIST_ITEMS:
            break
    return sources


def _extract_ptz_configurations(root: ET.Element | None) -> list[dict[str, Any]]:
    configs = []
    if root is None:
        return configs
    for node in root.iter():
        if _local_name(node.tag) != "PTZConfiguration":
            continue
        configs.append({
            "token": node.attrib.get("token"),
            "name": _first_text(node, "Name"),
            "node_token": _first_text(node, "NodeToken"),
            "default_timeout": _first_text(node, "DefaultPTZTimeout"),
        })
        if len(configs) >= MAX_ONVIF_LIST_ITEMS:
            break
    return configs


def _extract_ptz_nodes(root: ET.Element | None) -> list[dict[str, Any]]:
    nodes = []
    if root is None:
        return nodes
    for node in root.iter():
        if _local_name(node.tag) != "PTZNode":
            continue
        auxiliary = []
        for child in node.iter():
            if _local_name(child.tag) == "AuxiliaryCommands" and child.text:
                auxiliary.append(child.text.strip())
        nodes.append({
            "token": node.attrib.get("token"),
            "name": _first_text(node, "Name"),
            "home_supported": _first_text(node, "HomeSupported"),
            "maximum_presets": _first_text(node, "MaximumNumberOfPresets"),
            "auxiliary_commands": auxiliary,
        })
        if len(nodes) >= MAX_ONVIF_LIST_ITEMS:
            break
    return nodes


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
    # Do not perform a connect-only TCP pre-probe here. Some stock JOOAN
    # firmware appears to mishandle sockets that are opened and closed without
    # an ONVIF request. Go straight to a valid SOAP request instead.
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
        result["reachable"] = True
        # 401 proves the service/path exists and retrying alternate paths only
        # adds load. Only try fallbacks for an explicit 404.
        if response["status"] == 200 or response["status"] == 401:
            break
        if response["status"] != 404:
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

    # Device diagnostics: read-only calls against the confirmed device service.
    device_ops = [
        ("device_information", "<tds:GetDeviceInformation/>", "GetDeviceInformation", _extract_device_information),
        ("system_datetime", "<tds:GetSystemDateAndTime/>", "GetSystemDateAndTime", _extract_system_datetime),
        ("network_interfaces", "<tds:GetNetworkInterfaces/>", "GetNetworkInterfaces", _extract_network_interfaces),
        ("scopes", "<tds:GetScopes/>", "GetScopes", _extract_scopes),
        (
            "services_list",
            "<tds:GetServices><tds:IncludeCapability>false</tds:IncludeCapability></tds:GetServices>",
            "GetServices",
            _extract_services_list,
        ),
    ]
    result["device_diagnostics"] = {}
    for key, payload, action_name, parser in device_ops:
        response = _soap_post(
            host,
            port,
            result["path"],
            _soap_envelope(ONVIF_DEVICE, "tds", payload),
            action=f"{ONVIF_DEVICE}/{action_name}",
            timeout=timeout,
        )
        entry = {"http": response["status"], "authentication_required": response["authentication_required"]}
        if response["status"] == 200:
            root = _parse_xml(response["body"])
            entry["data"] = parser(root)
            fault = _soap_fault(root)
            if fault:
                entry["fault"] = fault
        elif response["error"]:
            entry["error"] = response["error"]
        result["device_diagnostics"][key] = entry
        if response["status"] is None or response["status"] == 401:
            # A transport failure or authentication gate will generally affect
            # the remaining device-service reads too. Stop instead of issuing
            # a series of redundant timeouts/401s.
            return result

    media_path = (services.get("media") or {}).get("path")
    if media_path:
        video_sources_response = _soap_post(
            host,
            port,
            media_path,
            _soap_envelope(ONVIF_MEDIA, "trt", "<trt:GetVideoSources/>"),
            action=f"{ONVIF_MEDIA}/GetVideoSources",
            timeout=timeout,
        )
        result["video_sources_http"] = video_sources_response["status"]
        if video_sources_response["status"] is None or video_sources_response["status"] == 401:
            result["media_error"] = video_sources_response["error"] or "ONVIF media authentication required"
            return result
        result["video_sources"] = (
            _extract_video_sources(_parse_xml(video_sources_response["body"]))
            if video_sources_response["status"] == 200
            else []
        )

        audio_sources_response = _soap_post(
            host,
            port,
            media_path,
            _soap_envelope(ONVIF_MEDIA, "trt", "<trt:GetAudioSources/>"),
            action=f"{ONVIF_MEDIA}/GetAudioSources",
            timeout=timeout,
        )
        result["audio_sources_http"] = audio_sources_response["status"]
        if audio_sources_response["status"] is None or audio_sources_response["status"] == 401:
            result["media_error"] = audio_sources_response["error"] or "ONVIF media authentication required"
            return result
        result["audio_sources"] = (
            _extract_audio_sources(_parse_xml(audio_sources_response["body"]))
            if audio_sources_response["status"] == 200
            else []
        )

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
        if profiles_response["status"] is None or profiles_response["status"] == 401:
            result["media_error"] = profiles_response["error"] or "ONVIF media authentication required"
            return result

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
                        f"<trt:ProfileToken>{xml_escape(str(token))}</trt:ProfileToken>"
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
                if stream_response["status"] is None or stream_response["status"] == 401:
                    profile["stream"] = None
                    profile["stream_error"] = stream_response["error"] or "ONVIF media authentication required"
                    result["profiles"] = profiles
                    return result
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

            nodes_response = _soap_post(
                host,
                port,
                ptz_path,
                _soap_envelope(ONVIF_PTZ, "tptz", "<tptz:GetNodes/>"),
                action=f"{ONVIF_PTZ}/GetNodes",
                timeout=timeout,
            )
            ptz["nodes_http"] = nodes_response["status"]
            if nodes_response["status"] is None or nodes_response["status"] == 401:
                ptz["error"] = nodes_response["error"] or "ONVIF PTZ authentication required"
                result["ptz"] = ptz
                return result
            ptz["nodes"] = (
                _extract_ptz_nodes(_parse_xml(nodes_response["body"]))
                if nodes_response["status"] == 200
                else []
            )

            configurations_response = _soap_post(
                host,
                port,
                ptz_path,
                _soap_envelope(ONVIF_PTZ, "tptz", "<tptz:GetConfigurations/>"),
                action=f"{ONVIF_PTZ}/GetConfigurations",
                timeout=timeout,
            )
            ptz["configurations_http"] = configurations_response["status"]
            if configurations_response["status"] is None or configurations_response["status"] == 401:
                ptz["error"] = configurations_response["error"] or "ONVIF PTZ authentication required"
                result["ptz"] = ptz
                return result
            ptz["configurations"] = (
                _extract_ptz_configurations(_parse_xml(configurations_response["body"]))
                if configurations_response["status"] == 200
                else []
            )

            status_request = _soap_envelope(
                ONVIF_PTZ,
                "tptz",
                (
                    "<tptz:GetStatus>"
                    f"<tptz:ProfileToken>{xml_escape(str(token))}</tptz:ProfileToken>"
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
            if status_response["status"] is None or status_response["status"] == 401:
                ptz["error"] = status_response["error"] or "ONVIF PTZ authentication required"
                result["ptz"] = ptz
                return result
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
                    f"<tptz:ProfileToken>{xml_escape(str(token))}</tptz:ProfileToken>"
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
        message = _redact_process_output(proc.stderr.decode("utf-8", "replace").strip())
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


def start_mjpeg_rtsp(
    url: str,
    *,
    width: int = 640,
    fps: int = 6,
    quality: int = 7,
) -> subprocess.Popen[bytes]:
    """Start one low-rate MJPEG bridge for an already allowlisted RTSP URL.

    The authenticated RTSP URL is passed only to ffmpeg and is never returned
    to the browser. stderr is discarded deliberately so ffmpeg cannot echo the
    URL (and credentials) into application logs.
    """
    safe_width = max(320, min(int(width), 960))
    safe_fps = max(2, min(int(fps), 10))
    safe_quality = max(3, min(int(quality), 12))
    return subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "500000",
            "-probesize",
            "65536",
            "-i",
            url,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"fps={safe_fps},scale={safe_width}:-2:flags=fast_bilinear",
            "-q:v",
            str(safe_quality),
            "-f",
            "mpjpeg",
            "-boundary_tag",
            "jooanframe",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
