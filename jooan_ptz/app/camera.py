import hashlib
import json
import logging
import re
from urllib.parse import urljoin

import requests

_LOGGER = logging.getLogger("jooan_ptz.camera")


class JooanCamera:
    def __init__(self, ip: str, username: str, password: str, timeout: float = 5.0, debug: bool = True):
        self.ip = ip.strip().rstrip("/")
        self.username = username.strip() or "admin"
        self.password = password
        self.timeout = timeout
        self.debug = debug
        self.userkey = hashlib.md5(password.encode("utf-8")).hexdigest()

    @property
    def base_url(self):
        return f"http://{self.ip}"

    def _get(self, endpoint: str, params=None):
        query = {"userid": self.username, "userkey": self.userkey}
        if params:
            query.update(params)

        if self.debug:
            _LOGGER.debug("REQUEST: GET %s", endpoint)
            _LOGGER.debug("REQUEST parameters: userid=%s", self.username)

        try:
            response = requests.get(
                urljoin(self.base_url + "/", endpoint.lstrip("/")),
                params=query,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            _LOGGER.warning("Request %s failed: %s", endpoint, exc)
            raise

        if self.debug:
            _LOGGER.debug("RESPONSE: HTTP %s", response.status_code)
            _LOGGER.debug("RESPONSE headers: %s", dict(response.headers))
            _LOGGER.debug("RESPONSE content-type: %s", response.headers.get("Content-Type", ""))
            _LOGGER.debug("RESPONSE body: %s", response.text)

        return response

    @staticmethod
    def _parse_camera_response(text: str):
        """Parse the camera's JSON, including its HTML-wrapped response format."""
        raw = text.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # The camera currently returns: <html><h2>{JSON}</h2><br></html>
        match = re.search(r"<h2>\s*(\{.*?\})\s*</h2>", raw, re.IGNORECASE | re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback for any future response containing a JSON object.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass

        return {"raw": text}

    def command(self, direction: str):
        allowed = {"up", "down", "left", "right", "stop"}
        if direction not in allowed:
            raise ValueError("Unsupported PTZ command")

        response = self._get("/goform/SingleHandlebyCommand", {"singleCMD": direction})
        response.raise_for_status()
        return self._parse_camera_response(response.text)

    def get_platform_id(self):
        response = self._get("/goform/getPlatformID")
        response.raise_for_status()
        return self._parse_camera_response(response.text)

    def get_network_state(self):
        response = self._get("/goform/getNetWorkState")
        response.raise_for_status()
        return self._parse_camera_response(response.text)

    def test(self):
        data = self.get_platform_id()
        if isinstance(data, dict) and data.get("result") == "error_passwd":
            raise PermissionError("Camera rejected the credentials")
        return data
