from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin

import requests

_LOGGER = logging.getLogger("jooan_ptz.camera")

FEATURE_KEY_LENS_MODE = "10008"
FEATURE_VALUE_DOUBLE_LENS = "double"


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
    raw_properties: dict | None = None
    raw_features: dict | None = None

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
            "raw_properties": self.raw_properties or {},
            "raw_features": self.raw_features or {},
        }


def _to_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class JooanCamera:
    """Local-only client for the JOOAN HTTP CGI endpoints."""

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        http_port: int = 80,
        features_port: int = 9898,
        rtsp_port: int = 554,
        timeout: float = 5.0,
        debug: bool = False,
    ):
        self.ip = self._validate_local_ip(ip)
        self.username = username.strip() or "admin"
        self.password = password
        self.http_port = int(http_port)
        self.features_port = int(features_port)
        self.rtsp_port = int(rtsp_port)
        self.timeout = timeout
        self.debug = debug
        self.userkey = hashlib.md5(password.encode("utf-8")).hexdigest()
        self._session = requests.Session()

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
        if not (addr.is_private or addr.is_link_local):
            raise ValueError(
                "Camera IP is not private/local. This add-on intentionally blocks public Internet targets"
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
        return re.sub(r"([?&]userkey=)[^&]*", r"\1<redacted>", url)

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
            response = self._session.get(url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            raise JooanNetworkError(f"Request to {endpoint} failed: {exc}") from exc

        self._debug_log("RESPONSE: HTTP %s %s", response.status_code, endpoint)
        self._debug_log("RESPONSE body: %s", response.text)
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
        allowed = {"up", "down", "left", "right", "stop"}
        if direction not in allowed:
            raise ValueError("Unsupported PTZ command")
        return self._goform("/goform/SingleHandlebyCommand", {"singleCMD": direction})

    def get_platform_id(self) -> dict:
        return self._goform("/goform/getPlatformID")

    def get_network_state(self) -> dict:
        return self._goform("/goform/getNetWorkState")

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

        lens_mode = features.get(FEATURE_KEY_LENS_MODE)
        channel_count = 2 if lens_mode == FEATURE_VALUE_DOUBLE_LENS else 1
        if lens_mode not in (FEATURE_VALUE_DOUBLE_LENS, "single", None):
            _LOGGER.warning(
                "Unknown deviceFeatures[%s]=%r; assuming one RTSP channel",
                FEATURE_KEY_LENS_MODE,
                lens_mode,
            )

        return DeviceInfo(
            device_id=properties.get("device_id"),
            model=properties.get("device_model"),
            mac=properties.get("device_mac"),
            firmware_version=(str(properties.get("device_version")) if properties.get("device_version") is not None else None),
            timezone=properties.get("timezone"),
            channel_count=channel_count,
            sdcard_total_mb=_to_int(properties.get("sdcard_total")),
            sdcard_free_mb=_to_int(properties.get("sdcard_free")),
            raw_properties=properties,
            raw_features=features,
        )

    def get_rtsp_credentials(self) -> tuple[str, str]:
        data = self._goform("/goform/getOtherSetttings", {"singleCMD": "RtspConf"})
        return str(data.get("user") or self.username), str(data.get("key") or self.password)

    def build_rtsp_url(self, channel: int, username: str, password: str) -> str:
        user = quote(username, safe="")
        key = quote(password, safe="")
        return f"rtsp://{user}:{key}@{self._url_host}:{self.rtsp_port}/live/ch{int(channel):02d}_0"

    def stream_summary(self, channel_count: int) -> dict:
        username, password = self.get_rtsp_credentials()
        return {
            "available": True,
            "channel_count": max(1, int(channel_count)),
            "paths": [f"/live/ch{channel:02d}_0" for channel in range(max(1, int(channel_count)))],
            "credentials_confirmed": bool(username and password),
        }

    def test(self) -> dict:
        return self.get_platform_id()
