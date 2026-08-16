import hashlib
from urllib.parse import urljoin

import requests


class JooanCamera:
    def __init__(self, ip: str, password: str, timeout: float = 5.0):
        self.ip = ip.strip().rstrip("/")
        self.password = password
        self.timeout = timeout
        self.userkey = hashlib.md5(password.encode("utf-8")).hexdigest()

    @property
    def base_url(self):
        return f"http://{self.ip}"

    def _get(self, endpoint: str, params=None):
        query = {"userid": "admin", "userkey": self.userkey}
        if params:
            query.update(params)
        return requests.get(
            urljoin(self.base_url + "/", endpoint.lstrip("/")),
            params=query,
            timeout=self.timeout,
        )

    def command(self, direction: str):
        allowed = {"up", "down", "left", "right", "stop"}
        if direction not in allowed:
            raise ValueError("Unsupported PTZ command")

        response = self._get(
            "/goform/SingleHandlebyCommand",
            {"singleCMD": direction},
        )
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            return {"result": "invalid_response", "raw": response.text}

        return data

    def get_platform_id(self):
        response = self._get("/goform/getPlatformID")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def get_network_state(self):
        response = self._get("/goform/getNetWorkState")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def test(self):
        data = self.get_platform_id()
        if isinstance(data, dict) and data.get("result") == "error_passwd":
            raise PermissionError("Camera rejected the password")
        return data
