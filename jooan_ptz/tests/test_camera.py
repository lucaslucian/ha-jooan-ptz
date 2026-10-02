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
        "GET http://10.0.0.10/path?userid=admin&userkey=deadbeef "
        "rtsp://admin:super-secret@10.0.0.10:554/live/ch00_0"
    )
    safe = redact_secrets(message)
    assert "deadbeef" not in safe
    assert "super-secret" not in safe
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
