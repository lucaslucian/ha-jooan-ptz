import camera as camera_module
import pytest

from camera import (
    JooanCamera,
    RTSP_PATH_CANDIDATES,
    _capabilities,
    _safe_properties,
    _state_from_properties,
    redact_secrets,
)


def test_private_camera_ip_is_accepted():
    camera = JooanCamera("192.168.1.20", "admin", "secret")
    assert camera.ip == "192.168.1.20"


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "100.64.0.1", "203.0.113.10", "127.0.0.1", "0.0.0.0"])
def test_non_lan_camera_ip_is_rejected(address):
    with pytest.raises(ValueError):
        JooanCamera(address, "admin", "secret")


def test_html_wrapped_json_is_parsed():
    data = JooanCamera._parse_camera_response(
        '<html><h2>{"result":"successful","model":"JA-A12"}</h2></html>'
    )
    assert data["model"] == "JA-A12"


def test_safe_properties_drop_unknown_secrets():
    raw = {
        "device_model": "JA-A12",
        "md_enable": 1,
        "device_pwd": "must-not-leak",
        "security_password": "must-not-leak",
        "unknown_future_field": "must-not-auto-expose",
    }
    safe = _safe_properties(raw)
    assert safe["device_model"] == "JA-A12"
    assert safe["md_enable"] == 1
    assert "device_pwd" not in safe
    assert "security_password" not in safe
    assert "unknown_future_field" not in safe


def test_local_state_is_read_only_subset():
    raw = {
        "md_enable": 1,
        "autotrack": 0,
        "record_enable": 1,
        "device_model": "JA-A12",
    }
    state = _state_from_properties(raw)
    assert state == {"record_enable": 1, "md_enable": 1, "autotrack": 0}


def test_capability_derivation():
    caps = _capabilities(
        {"md_enable": 1, "autotrack": 0, "record_enable": 1, "sdcard_status": 0},
        {"10007": "H264", "10008": "double", "10043": "4x|16x"},
    )
    assert caps["codec"] == "H264"
    assert caps["dual_lens"] is True
    assert caps["playback_fast_forward"] == "4x|16x"
    assert caps["motion_detection"] is True
    assert caps["automatic_tracking"] is True


def test_rtsp_path_allowlist():
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    for path in RTSP_PATH_CANDIDATES:
        url = camera.build_rtsp_url_path(path, "admin", "rtsp-secret")
        assert path in url
    with pytest.raises(ValueError):
        camera.build_rtsp_url_path("/live/../../bad", "admin", "secret")


def test_ptz_command_is_allowlisted(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    with pytest.raises(ValueError):
        camera.command("SetDiagMode")


def test_redact_secrets_hides_userkey_and_rtsp_password():
    message = (
        'GET http://10.0.0.10/path?userid=admin&userkey=deadbeef '
        'rtsp://admin:super-secret@10.0.0.10:554/live/ch00_0 '
        '{"key":"json-secret"}'
    )
    safe = redact_secrets(message)
    assert "deadbeef" not in safe
    assert "super-secret" not in safe
    assert "json-secret" not in safe
    assert "userkey=<redacted>" in safe
    assert "rtsp://admin:<redacted>@10.0.0.10" in safe


def test_check_auth_uses_ptz_stop(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    calls = []

    def fake_goform(endpoint, params=None):
        calls.append((endpoint, params))
        return {"result": "success"}

    monkeypatch.setattr(camera, "_goform", fake_goform)
    result = camera.check_auth()

    assert result == {"result": "success"}
    assert calls == [
        ("/goform/SingleHandlebyCommand", {"singleCMD": "stop"})
    ]


def test_heartbeat_uses_icmp_without_touching_camera_ports(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret", http_port=8080)
    calls = []

    def fake_icmp_probe(host, timeout=1.5):
        calls.append((host, timeout))
        return {"online": True, "method": "icmp", "error": None}

    monkeypatch.setattr(camera_module, "icmp_probe", fake_icmp_probe)
    result = camera.heartbeat()

    assert result == {"online": True, "method": "icmp", "error": None}
    assert calls == [("10.0.0.10", 1.5)]


def test_rtsp_credentials_are_cached(monkeypatch):
    camera_module._rtsp_credential_cache.clear()
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    calls = []

    def fake_goform(endpoint, params=None):
        calls.append((endpoint, params))
        return {"user": "admin", "key": "rtsp-secret"}

    monkeypatch.setattr(camera, "_goform", fake_goform)
    first = camera.get_rtsp_credentials()
    second = camera.get_rtsp_credentials()

    assert first == ("admin", "rtsp-secret")
    assert second == first
    assert len(calls) == 1


def test_device_features_tolerates_non_object_sections(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")

    class Response:
        text = '{"result":"successful","properties":[],"deviceFeatures":"invalid"}'

    monkeypatch.setattr(camera, "_get", lambda *args, **kwargs: Response())
    info = camera.get_device_features()

    assert info.channel_count == 1
    assert info.safe_properties == {}
    assert info.device_features == {}
    assert info.local_state == {}


def test_audio_only_rtsp_result_does_not_count_as_working_video(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    monkeypatch.setattr(camera, "get_rtsp_credentials", lambda: ("admin", "rtsp"))
    monkeypatch.setattr(camera_module.time, "sleep", lambda _: None)

    def fake_ffprobe(url, timeout=None):
        if "/live/ch00_0" in url:
            return {
                "available": True,
                "error": None,
                "streams": [{"codec_type": "audio", "codec_name": "pcm_alaw"}],
            }
        return {
            "available": True,
            "error": None,
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }

    monkeypatch.setattr(camera_module, "ffprobe_rtsp", fake_ffprobe)
    result = camera.probe_rtsp_streams(channel_count=1)

    assert [item["path"] for item in result["streams"]] == [
        "/live/ch00_0",
        "/live/ch00_1",
    ]
    assert result["reachable"] is True


def test_heartbeat_preserves_unknown_icmp_state(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")

    monkeypatch.setattr(
        camera_module,
        "icmp_probe",
        lambda *args, **kwargs: {
            "online": None,
            "method": "icmp",
            "error": "ICMP heartbeat is unavailable in this container",
        },
    )

    assert camera.heartbeat()["online"] is None


def test_preview_substream_probe_is_sequential(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    monkeypatch.setattr(camera, "get_rtsp_credentials", lambda: ("admin", "rtsp"))
    monkeypatch.setattr(camera_module.time, "sleep", lambda _: None)

    calls = []

    def fake_ffprobe(url, timeout=None):
        calls.append(url)
        return {
            "available": True,
            "error": None,
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }

    monkeypatch.setattr(camera_module, "ffprobe_rtsp", fake_ffprobe)
    result = camera.probe_preview_substreams(2)

    assert [item["path"] for item in result["streams"]] == [
        "/live/ch00_1",
        "/live/ch01_1",
    ]
    assert result["probe_mode"] == "substreams-only"
    assert len(calls) == 2


def test_start_mjpeg_preview_uses_allowlisted_stream(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    monkeypatch.setattr(camera, "get_rtsp_credentials", lambda: ("admin", "rtsp-secret"))
    calls = []
    sentinel = object()

    def fake_start(url, width=640, fps=6):
        calls.append((url, width, fps))
        return sentinel

    monkeypatch.setattr(camera_module, "start_mjpeg_rtsp", fake_start)

    assert camera.start_mjpeg_preview("ch00_1", width=640, fps=6) is sentinel
    assert "/live/ch00_1" in calls[0][0]
    assert "rtsp-secret" in calls[0][0]

    with pytest.raises(ValueError):
        camera.start_mjpeg_preview("../../bad")


def test_preview_substream_probe_can_target_only_ptz_channel(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    monkeypatch.setattr(camera, "get_rtsp_credentials", lambda: ("admin", "rtsp"))
    calls = []

    def fake_ffprobe(url, timeout=None):
        calls.append(url)
        return {
            "available": True,
            "error": None,
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }

    monkeypatch.setattr(camera_module, "ffprobe_rtsp", fake_ffprobe)
    result = camera.probe_preview_substreams(2, channels=[0])

    assert result["tested_channels"] == [0]
    assert [item["path"] for item in result["streams"]] == ["/live/ch00_1"]
    assert len(calls) == 1

def test_post_form_uses_direct_session_and_keeps_credentials_out_of_form(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"result:success"
        text = "result:success"
        headers = {"Content-Type": "text/plain"}

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            captured["session"] = self
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, params, data, timeout, headers):
            captured.update({
                "url": url,
                "params": dict(params),
                "data": dict(data),
                "timeout": timeout,
                "headers": dict(headers),
                "trust_env_at_post": self.trust_env,
            })
            return FakeResponse()

    monkeypatch.setattr(camera_module.requests, "Session", FakeSession)

    response = camera._post_form(
        "/goform/NTP",
        {"time_zone": "AST_-04"},
        authenticated=True,
    )

    assert response.status_code == 200
    assert captured["url"] == "http://10.0.0.10:80/goform/NTP"
    assert captured["params"]["userid"] == "admin"
    assert "userkey" in captured["params"]
    assert captured["data"] == {"time_zone": "AST_-04"}
    assert "userid" not in captured["data"]
    assert "userkey" not in captured["data"]
    assert captured["trust_env_at_post"] is False
    assert captured["headers"]["Connection"] == "close"

def test_post_query_keeps_fields_in_query_and_uses_fixed_body(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "secret")
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"motionEnable:YES"
        text = "motionEnable:YES"
        headers = {"Content-Type": "text/plain"}

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, params, data, timeout, headers):
            captured.update({
                "url": url,
                "params": dict(params),
                "data": data,
                "trust_env_at_post": self.trust_env,
            })
            return FakeResponse()

    monkeypatch.setattr(camera_module.requests, "Session", FakeSession)

    response = camera._post_query(
        "/goform/getmotiondetectSettings",
        {"motionEnable": "", "sensitivity": ""},
        body="n/a",
    )

    assert response.status_code == 200
    assert captured["url"] == "http://10.0.0.10:80/goform/getmotiondetectSettings"
    assert captured["params"]["userid"] == "admin"
    assert captured["params"]["motionEnable"] == ""
    assert captured["params"]["sensitivity"] == ""
    assert captured["data"] == "n/a"
    assert captured["trust_env_at_post"] is False

