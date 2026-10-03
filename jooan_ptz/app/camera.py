from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urljoin

import requests

from probe import capture_snapshot, ffprobe_rtsp, icmp_probe, onvif_probe, start_mjpeg_rtsp

_LOGGER = logging.getLogger("jooan_ptz.camera")

FEATURE_KEY_CODEC = "10007"
FEATURE_KEY_LENS_MODE = "10008"
FEATURE_KEY_PLAYBACK_SPEED = "10043"
FEATURE_VALUE_DOUBLE_LENS = "double"
RTSP_CREDENTIAL_TTL = 300.0
RTSP_PROBE_GAP = 0.5
MAX_RTSP_PROBES = 4

_rtsp_credential_lock = threading.Lock()
_rtsp_credential_cache: dict[tuple[str, int, str, str], tuple[float, str, str]] = {}

# Only fields observed in local get_deviceFeatures captures and considered safe
# to expose in diagnostics. Unknown properties are deliberately not returned.
SAFE_PROPERTY_KEYS = {
    "SupportFormatProg",
    "alarm_light_mode",
    "alarm_light_schedule",
    "alarm_light_switch",
    "alarmsoundselect",
    "audiosensitive",
    "autotrack",
    "buzzer",
    "definition",
    "device_id",
    "device_ip",
    "device_mac",
    "device_model",
    "device_version",
    "flipmirror",
    "floodlight",
    "from_type",
    "lastformattime",
    "led",
    "light_schedule",
    "md_enable",
    "mdarea",
    "mdsensitivity",
    "msgpush_enable",
    "msgpush_schedule",
    "newflood_light_schedule",
    "newmsg_push_schedule",
    "newrecord_schedule",
    "pdarea",
    "person_detect",
    "person_track_enable",
    "powerfrequency",
    "ptz_covre_status",
    "ptz_hide_mode",
    "ptz_hide_schedule",
    "qualitymode",
    "recloopnum",
    "record_enable",
    "record_schedule",
    "record_type",
    "recordechannel",
    "rectype",
    "resolution",
    "sdcard_excepreason",
    "sdcard_free",
    "sdcard_status",
    "sdcard_total",
    "solution",
    "sound_alarm_schedule",
    "sub_md_enable",
    "sub_mdarea",
    "sub_mdsensitivity",
    "timezone",
    "vehicle_detect",
    "video_standard_red",
    "week",
    "yellowlight",
}

RTSP_PATH_CANDIDATES = (
    # Main streams first. These two paths were previously confirmed manually
    # on the tested dual-lens JA-A12 and should not compete with substream
    # probes for the camera's limited RTSP session capacity.
    "/live/ch00_0",
    "/live/ch01_0",
    "/live/ch00_1",
    "/live/ch01_1",
)

RTSP_DISCOVERED_PATH_RE = re.compile(
    r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{1,160}$"
)

class JooanCameraError(Exception):
    """Base error for local JOOAN communication."""


class JooanAuthError(JooanCameraError):
    """Raised when the camera rejects the configured credentials."""


class JooanNetworkError(JooanCameraError):
    """Raised when a local camera request fails."""


@dataclass(slots=True)
class DeviceInfo:
    device_id: str | None = None
    model: str | None = None
    mac: str | None = None
    firmware_version: str | None = None
    timezone: str | None = None
    channel_count: int = 1
    sdcard_total_mb: int | None = None
    sdcard_free_mb: int | None = None
    safe_properties: dict | None = None
    device_features: dict | None = None
    local_state: dict | None = None
    capabilities: dict | None = None

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "model": self.model,
            "mac": self.mac,
            "firmware_version": self.firmware_version,
            "timezone": self.timezone,
            "channel_count": self.channel_count,
            "sdcard_total_mb": self.sdcard_total_mb,
            "sdcard_free_mb": self.sdcard_free_mb,
            "properties": self.safe_properties or {},
            "device_features": self.device_features or {},
            "local_state": self.local_state or {},
            "capabilities": self.capabilities or {},
        }


def redact_secrets(value: object) -> str:
    """Remove camera/RTSP credentials from messages before logging or returning them."""
    text = str(value)
    text = re.sub(r"([?&]userkey=)[^&\s'\"]+", r"\1<redacted>", text, flags=re.I)
    text = re.sub(
        r"([?&](?:key|password|AuthKey)=)[^&\s'\"]+",
        r"\1<redacted>",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(rtsp://[^:/@\s]+:)[^@\s]+(@)",
        r"\1<redacted>\2",
        text,
        flags=re.I,
    )
    text = re.sub(
        r'("(?:key|password|AuthKey|userkey|device_pwd|security_password|authcode)"\s*:\s*")[^"]*(")',
        r"\1<redacted>\2",
        text,
        flags=re.I,
    )
    return text

def _to_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_properties(properties: dict) -> dict:
    return {key: properties[key] for key in SAFE_PROPERTY_KEYS if key in properties}


def _state_from_properties(properties: dict) -> dict:
    keys = (
        "sdcard_status",
        "sdcard_excepreason",
        "record_enable",
        "record_type",
        "recordechannel",
        "md_enable",
        "mdsensitivity",
        "mdarea",
        "sub_md_enable",
        "sub_mdsensitivity",
        "sub_mdarea",
        "autotrack",
        "person_track_enable",
        "person_detect",
        "vehicle_detect",
        "pdarea",
        "led",
        "floodlight",
        "yellowlight",
        "video_standard_red",
        "flipmirror",
        "ptz_covre_status",
        "ptz_hide_mode",
        "msgpush_enable",
        "audiosensitive",
        "buzzer",
        "qualitymode",
        "powerfrequency",
    )
    return {key: properties.get(key) for key in keys if key in properties}


def _capabilities(properties: dict, features: dict) -> dict:
    return {
        "codec": features.get(FEATURE_KEY_CODEC),
        "lens_mode": features.get(FEATURE_KEY_LENS_MODE),
        "dual_lens": features.get(FEATURE_KEY_LENS_MODE) == FEATURE_VALUE_DOUBLE_LENS,
        "playback_fast_forward": features.get(FEATURE_KEY_PLAYBACK_SPEED),
        "motion_detection": "md_enable" in properties,
        "secondary_motion_detection": "sub_md_enable" in properties,
        "automatic_tracking": "autotrack" in properties,
        "person_tracking": "person_track_enable" in properties,
        "person_detection": "person_detect" in properties,
        "vehicle_detection": "vehicle_detect" in properties,
        "recording": "record_enable" in properties,
        "sdcard": "sdcard_status" in properties or "sdcard_total" in properties,
        "led": "led" in properties,
        "floodlight": "floodlight" in properties,
        "yellow_light": "yellowlight" in properties,
        "video_standard_red": "video_standard_red" in properties,
        "ptz_hide": "ptz_hide_mode" in properties,
        "schedules": any(
            key in properties
            for key in (
                "record_schedule",
                "newrecord_schedule",
                "light_schedule",
                "newflood_light_schedule",
                "ptz_hide_schedule",
                "msgpush_schedule",
                "newmsg_push_schedule",
                "sound_alarm_schedule",
            )
        ),
    }


class JooanCamera:
    """Local-only client for JOOAN HTTP/RTSP/ONVIF endpoints."""

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        http_port: int = 80,
        features_port: int = 9898,
        rtsp_port: int = 554,
        onvif_port: int = 8899,
        timeout: float = 5.0,
        debug: bool = False,
    ):
        self.ip = self._validate_local_ip(ip)
        self.username = username.strip() or "admin"
        self.password = password
        self.http_port = int(http_port)
        self.features_port = int(features_port)
        self.rtsp_port = int(rtsp_port)
        self.onvif_port = int(onvif_port)
        self.timeout = timeout
        self.debug = debug
        self.userkey = hashlib.md5(password.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_local_ip(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            raise ValueError("Camera IP is not configured")
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValueError(
                "Camera address must be a literal private/local IP address; hostnames are not accepted"
            ) from exc

        if addr.is_unspecified or addr.is_loopback or addr.is_multicast:
            raise ValueError("Camera IP must point to a LAN device")

        if addr.version == 4:
            allowed = any(
                addr in ipaddress.ip_network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
            )
        else:
            allowed = any(
                addr in ipaddress.ip_network(network)
                for network in ("fc00::/7", "fe80::/10")
            )

        if not allowed:
            raise ValueError(
                "Camera IP is not RFC1918/ULA/link-local. This app intentionally blocks public Internet targets"
            )
        return raw

    @property
    def _url_host(self) -> str:
        return f"[{self.ip}]" if ":" in self.ip else self.ip

    @property
    def base_url(self) -> str:
        return f"http://{self._url_host}:{self.http_port}"

    def _debug_log(self, message: str, *args) -> None:
        if self.debug:
            _LOGGER.info(message, *args)

    @staticmethod
    def _safe_url(url: str) -> str:
        return redact_secrets(url)

    def _get(self, endpoint: str, params=None, *, authenticated: bool = True, port: int | None = None):
        query = {}
        if authenticated:
            query.update({"userid": self.username, "userkey": self.userkey})
        if params:
            query.update(params)

        base = self.base_url if port is None else f"http://{self._url_host}:{port}"
        url = urljoin(base + "/", endpoint.lstrip("/"))
        prepared = requests.Request("GET", url, params=query).prepare().url or url
        self._debug_log("REQUEST: GET %s", self._safe_url(prepared))

        try:
            # Use a short-lived Session so environment HTTP(S)_PROXY settings
            # can never route private camera traffic outside the LAN. The
            # response body is eagerly loaded before the Session is closed.
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    url,
                    params=query,
                    timeout=self.timeout,
                    headers={"Connection": "close"},
                )
                _ = response.content
        except requests.RequestException as exc:
            raise JooanNetworkError(
                f"Request to {endpoint} failed: {redact_secrets(exc)}"
            ) from None

        self._debug_log("RESPONSE: HTTP %s %s", response.status_code, endpoint)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise JooanNetworkError(
                f"Camera returned HTTP {response.status_code} for {endpoint}"
            ) from exc
        return response

    @staticmethod
    def _parse_camera_response(text: str) -> dict:
        raw = text.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None

        if isinstance(data, dict):
            return data

        match = re.search(r"<h2>\s*(\{.*?\})\s*</h2>", raw, re.IGNORECASE | re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass

        raise JooanCameraError("Camera returned a response without recognizable JSON")

    @staticmethod
    def _check_auth(data: dict) -> dict:
        if data.get("result") == "error_passwd":
            raise JooanAuthError("Camera rejected the configured username/password")
        return data

    def _goform(self, endpoint: str, params=None) -> dict:
        response = self._get(endpoint, params=params, authenticated=True)
        return self._check_auth(self._parse_camera_response(response.text))

    def command(self, direction: str) -> dict:
        # Deliberate allowlist. Never turn this into a generic singleCMD proxy.
        allowed = {"up", "down", "left", "right", "stop"}
        if direction not in allowed:
            raise ValueError("Unsupported PTZ command")
        return self._goform("/goform/SingleHandlebyCommand", {"singleCMD": direction})

    def check_auth(self) -> dict:
        """Validate CGI credentials with the already-confirmed PTZ endpoint."""
        return self.command("stop")

    def get_platform_id(self) -> dict:
        return self._goform("/goform/getPlatformID")

    def get_network_state(self) -> dict:
        return self._goform("/goform/getNetWorkState")

    def get_ap_lan_p2p_support(self) -> dict:
        response = self._get("/goform/getAPLanP2PSupport", authenticated=False)
        data = self._parse_camera_response(response.text)
        # Keep only capability/identity values observed in local captures.
        allowed = {
            "result",
            "device_id",
            "resolution",
            "fullview",
            "persondet",
            "DetectType",
            "audioselect",
            "model",
            "isquery",
            "rtspAuth",
        }
        return {key: data[key] for key in allowed if key in data}

    def get_device_features(self) -> DeviceInfo:
        response = self._get(
            "/get",
            {"singleCMD": "get_deviceFeatures"},
            authenticated=False,
            port=self.features_port,
        )
        data = self._parse_camera_response(response.text)
        properties = data.get("properties") or {}
        features = data.get("deviceFeatures") or {}
        if not isinstance(properties, dict):
            properties = {}
        if not isinstance(features, dict):
            features = {}

        lens_mode = features.get(FEATURE_KEY_LENS_MODE)
        channel_count = 2 if lens_mode == FEATURE_VALUE_DOUBLE_LENS else 1
        if lens_mode not in (FEATURE_VALUE_DOUBLE_LENS, "single", None):
            _LOGGER.warning(
                "Unknown deviceFeatures[%s]=%r; assuming one camera channel",
                FEATURE_KEY_LENS_MODE,
                lens_mode,
            )

        safe_properties = _safe_properties(properties)
        return DeviceInfo(
            device_id=properties.get("device_id"),
            model=properties.get("device_model"),
            mac=properties.get("device_mac"),
            firmware_version=(
                str(properties.get("device_version"))
                if properties.get("device_version") is not None
                else None
            ),
            timezone=properties.get("timezone"),
            channel_count=channel_count,
            sdcard_total_mb=_to_int(properties.get("sdcard_total")),
            sdcard_free_mb=_to_int(properties.get("sdcard_free")),
            safe_properties=safe_properties,
            device_features={str(k): v for k, v in features.items()},
            local_state=_state_from_properties(properties),
            capabilities=_capabilities(properties, features),
        )

    def get_rtsp_credentials(self, *, force: bool = False) -> tuple[str, str]:
        """Return RTSP credentials with a short in-memory cache.

        Snapshot refresh used to call RtspConf for every JPEG. On constrained
        firmware that creates unnecessary HTTP load. Cache is process-local,
        keyed by camera/config credentials and never exposed to the UI.
        """
        cache_key = (self.ip, self.http_port, self.username, self.userkey)
        now = time.monotonic()
        if not force:
            with _rtsp_credential_lock:
                cached = _rtsp_credential_cache.get(cache_key)
                if cached and now - cached[0] < RTSP_CREDENTIAL_TTL:
                    return cached[1], cached[2]

        data = self._goform("/goform/getOtherSetttings", {"singleCMD": "RtspConf"})
        credentials = (
            str(data.get("user") or self.username),
            str(data.get("key") or self.password),
        )
        with _rtsp_credential_lock:
            _rtsp_credential_cache[cache_key] = (now, *credentials)
        return credentials

    def build_rtsp_url_path(
        self,
        path: str,
        username: str,
        password: str,
        *,
        discovered: bool = False,
    ) -> str:
        if path not in RTSP_PATH_CANDIDATES:
            if (
                not discovered
                or not RTSP_DISCOVERED_PATH_RE.fullmatch(path)
                or ".." in path
            ):
                raise ValueError("Unsupported RTSP path")
        user = quote(username, safe="")
        key = quote(password, safe="")
        return f"rtsp://{user}:{key}@{self._url_host}:{self.rtsp_port}{path}"

    def build_rtsp_url(self, channel: int, username: str, password: str) -> str:
        path = f"/live/ch{int(channel):02d}_0"
        return self.build_rtsp_url_path(path, username, password)

    def stream_summary(self, channel_count: int) -> dict:
        username, password = self.get_rtsp_credentials()
        return {
            "credentials_confirmed": bool(username and password),
            "candidate_paths": list(RTSP_PATH_CANDIDATES),
            "reported_channel_count": max(1, int(channel_count)),
        }

    def probe_rtsp_streams(
        self,
        onvif_info: dict | None = None,
        *,
        channel_count: int = 2,
    ) -> dict:
        """Probe the minimum number of RTSP sessions needed.

        For each reported channel, try the confirmed main stream first and only
        fall back to the corresponding substream if the main stream fails.
        ONVIF-discovered paths are tried only while fewer working streams than
        reported channels have been found. A hard cap prevents a malformed
        profile list from opening an unbounded number of RTSP sessions.
        """
        username, password = self.get_rtsp_credentials()
        channel_count = max(1, min(int(channel_count or 1), 2))
        discovered_paths: list[str] = []
        for profile in (onvif_info or {}).get("profiles", []):
            stream = profile.get("stream") or {}
            path = stream.get("path")
            if (
                isinstance(path, str)
                and RTSP_DISCOVERED_PATH_RE.fullmatch(path)
                and ".." not in path
                and path not in RTSP_PATH_CANDIDATES
                and path not in discovered_paths
            ):
                discovered_paths.append(path)

        results: list[dict] = []
        working = 0

        def run_probe(path: str, *, discovered: bool = False) -> bool:
            if len(results) >= MAX_RTSP_PROBES:
                return False
            url = self.build_rtsp_url_path(
                path,
                username,
                password,
                discovered=discovered,
            )
            result = ffprobe_rtsp(url, timeout=5.0)
            results.append(
                {
                    "path": path,
                    "source": "onvif" if discovered else "known_candidate",
                    **result,
                }
            )
            time.sleep(RTSP_PROBE_GAP)
            return bool(
                result.get("available")
                and any(
                    isinstance(stream, dict) and stream.get("codec_type") == "video"
                    for stream in result.get("streams", [])
                )
            )

        for channel in range(channel_count):
            main_path = f"/live/ch{channel:02d}_0"
            sub_path = f"/live/ch{channel:02d}_1"
            if run_probe(main_path):
                working += 1
                continue
            if run_probe(sub_path):
                working += 1

        if working < channel_count:
            for path in discovered_paths:
                if len(results) >= MAX_RTSP_PROBES or working >= channel_count:
                    break
                if run_probe(path, discovered=True):
                    working += 1

        return {
            "port": self.rtsp_port,
            "reachable": working > 0,
            "probe_mode": "sequential-minimal",
            "reported_channel_count": channel_count,
            "streams": results,
        }

    def probe_preview_substreams(
        self,
        channel_count: int = 2,
        *,
        channels: list[int] | tuple[int, ...] | None = None,
    ) -> dict:
        """Validate low-bandwidth substreams only when explicitly requested."""
        username, password = self.get_rtsp_credentials()
        channel_count = max(1, min(int(channel_count or 1), 2))
        if channels is None:
            targets = list(range(channel_count))
        else:
            targets = []
            for channel in channels:
                channel = int(channel)
                if 0 <= channel < channel_count and channel not in targets:
                    targets.append(channel)
        results: list[dict] = []
        for index, channel in enumerate(targets):
            path = f"/live/ch{channel:02d}_1"
            url = self.build_rtsp_url_path(path, username, password)
            result = ffprobe_rtsp(url, timeout=5.0)
            results.append({"path": path, "source": "preview_validation", **result})
            if index + 1 < len(targets):
                time.sleep(RTSP_PROBE_GAP)
        return {
            "probe_mode": "substreams-only",
            "reported_channel_count": channel_count,
            "tested_channels": targets,
            "streams": results,
            "reachable": any(
                item.get("available")
                and any(
                    isinstance(stream, dict) and stream.get("codec_type") == "video"
                    for stream in item.get("streams", [])
                )
                for item in results
            ),
        }

    def start_mjpeg_preview(
        self,
        stream: str,
        *,
        width: int = 640,
        fps: int = 6,
    ):
        """Bridge one allowlisted RTSP stream to low-rate MJPEG."""
        path = stream if stream.startswith("/") else f"/live/{stream}"
        if path not in RTSP_PATH_CANDIDATES:
            raise ValueError("Unsupported RTSP preview stream")
        username, password = self.get_rtsp_credentials()
        url = self.build_rtsp_url_path(path, username, password)
        return start_mjpeg_rtsp(url, width=width, fps=fps)

    def probe_onvif(self) -> dict:
        return onvif_probe(self.ip, self.onvif_port)

    def heartbeat(self) -> dict:
        """Very cheap background liveness check using ICMP only."""
        probe = icmp_probe(self.ip, timeout=1.5)
        return {
            "online": probe.get("online"),
            "method": probe.get("method", "icmp"),
            "error": probe.get("error"),
        }

    def snapshot(self, stream: str) -> bytes:
        path = stream if stream.startswith("/") else f"/live/{stream}"
        if path not in RTSP_PATH_CANDIDATES:
            raise ValueError("Unsupported RTSP stream")
        username, password = self.get_rtsp_credentials()
        return capture_snapshot(self.build_rtsp_url_path(path, username, password))

    def test(self) -> dict:
        return self.check_auth()
