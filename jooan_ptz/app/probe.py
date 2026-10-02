from __future__ import annotations

import json
import logging
import socket
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("jooan_ptz.probe")


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


def onvif_probe(host: str, port: int = 8899, timeout: float = 2.5) -> dict[str, Any]:
    """Read-only ONVIF endpoint probe.

    It intentionally does not send PTZ or configuration actions. Status codes
    such as 401 or 405 are still useful: they prove an HTTP service is present.
    """
    result: dict[str, Any] = {
        "port": int(port),
        "reachable": False,
        "http_detected": False,
        "path": None,
        "status": None,
        "authentication_required": None,
        "error": None,
    }
    port_status = tcp_probe(host, port, timeout=min(timeout, 1.5))
    result["reachable"] = port_status.reachable
    if not port_status.reachable:
        result["error"] = port_status.error
        return result

    request_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        '<s:Body><tds:GetCapabilities><tds:Category>All</tds:Category>'
        '</tds:GetCapabilities></s:Body></s:Envelope>'
    ).encode("utf-8")

    for path in ("/onvif/device_service", "/onvif/Device", "/onvif/device"):
        wire = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{int(port)}\r\n"
            "Content-Type: application/soap+xml; charset=utf-8\r\n"
            f"Content-Length: {len(request_body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + request_body
        try:
            with socket.create_connection((host, int(port)), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(wire)
                raw = sock.recv(4096)
        except OSError as exc:
            result["error"] = str(exc)
            continue

        first = raw.split(b"\r\n", 1)[0].decode("latin1", "replace")
        if not first.startswith("HTTP/"):
            continue
        parts = first.split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        result.update(
            {
                "http_detected": True,
                "path": path,
                "status": status,
                "authentication_required": status == 401,
                "error": None,
            }
        )
        return result

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
