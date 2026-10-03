import xml.etree.ElementTree as ET

import lab
from camera import redact_secrets


class FakeCamera:
    ip = "10.0.0.10"
    onvif_port = 8899

    def __init__(self):
        self.goform_calls = []

    def _goform(self, endpoint, params):
        self.goform_calls.append((endpoint, dict(params)))
        return {"result": "success"}


def ptz_info(*, auxiliary=True):
    node = {
        "token": "node0",
        "auxiliary_commands": ["tt:Irlamp|On", "tt:Irlamp|Off"] if auxiliary else [],
    }
    return {
        "services": {
            "ptz": {"path": "/onvif/Ptz"},
            "imaging": {"path": "/onvif/Imaging"},
            "events": {"path": "/onvif/Events"},
        },
        "ptz": {
            "path": "/onvif/Ptz",
            "profile_token": "profile_0",
            "nodes": [node],
        },
        "profiles": [{"token": "profile_0"}],
        "video_sources": [{"token": "VideoSource"}],
    }


def ok_response():
    return {
        "status": 200,
        "body": b"",
        "error": None,
        "authentication_required": False,
    }


def test_onvif_move_is_bounded_and_always_stops(monkeypatch):
    calls = []

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        return ok_response(), ET.fromstring("<Envelope/>")

    monkeypatch.setattr(lab, "_soap", fake_soap)
    monkeypatch.setattr(lab.time, "sleep", lambda _: None)

    result = lab.onvif_continuous_move(
        FakeCamera(),
        ptz_info(),
        direction="left",
        speed=9,
        duration_ms=5000,
    )

    assert result["speed"] == 1.0
    assert result["duration_ms"] == 800
    assert [call["action"] for call in calls] == ["ContinuousMove", "Stop"]
    assert 'x="-1.000"' in calls[0]["payload"]
    assert "<tptz:PanTilt>true</tptz:PanTilt>" in calls[1]["payload"]


def test_ir_command_must_be_advertised(monkeypatch):
    camera = FakeCamera()
    try:
        lab.onvif_ir_lamp(camera, ptz_info(auxiliary=False), enabled=True)
    except lab.LabError as exc:
        assert "did not advertise" in str(exc)
    else:
        raise AssertionError("unadvertised auxiliary command must be rejected")


def test_ir_uses_only_fixed_advertised_command(monkeypatch):
    calls = []

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        return ok_response(), ET.fromstring("<Envelope/>")

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_ir_lamp(FakeCamera(), ptz_info(), enabled=True)

    assert result["command"] == "tt:Irlamp|On"
    assert calls[0]["action"] == "SendAuxiliaryCommand"
    assert "tt:Irlamp|On" in calls[0]["payload"]


def test_diag_mode_lab_only_exposes_fixed_disable():
    camera = FakeCamera()
    result = lab.disable_diag_mode(camera)

    assert result["accepted"] is True
    assert camera.goform_calls == [
        (
            "/goform/SingleHandlebyCommand",
            {"singleCMD": "SetDiagMode", "enable": "0"},
        )
    ]


def test_pullpoint_reported_host_is_never_followed(monkeypatch):
    calls = []
    create_root = ET.fromstring(
        """
        <Envelope xmlns:wsa="http://www.w3.org/2005/08/addressing">
          <SubscriptionReference>
            <wsa:Address>http://203.0.113.55:9999/onvif/subscription/abc</wsa:Address>
          </SubscriptionReference>
        </Envelope>
        """
    )
    pull_root = ET.fromstring(
        """
        <Envelope>
          <NotificationMessage>
            <Topic>tns1:RuleEngine/CellMotionDetector/Motion</Topic>
            <Message><Data><SimpleItem Name="IsMotion" Value="true"/></Data></Message>
          </NotificationMessage>
        </Envelope>
        """
    )

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        if kwargs["action"] == "CreatePullPointSubscription":
            return ok_response(), create_root
        return ok_response(), pull_root

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_pull_events(FakeCamera(), ptz_info())

    assert calls[0]["path"] == "/onvif/Events"
    assert calls[1]["path"] == "/onvif/subscription/abc"
    assert "203.0.113.55" not in calls[1]["path"]
    assert result["messages"][0]["items"] == [{"name": "IsMotion", "value": "true"}]


def test_only_ha_test_preset_can_be_deleted(monkeypatch):
    monkeypatch.setattr(
        lab,
        "onvif_list_presets",
        lambda camera, info: {
            "accepted": True,
            "presets": [{"token": "7", "name": "Porta"}],
        },
    )
    try:
        lab.onvif_delete_test_preset(FakeCamera(), ptz_info(), token="7")
    except lab.LabError as exc:
        assert "Only the HA_TEST preset" in str(exc)
    else:
        raise AssertionError("non-test preset deletion must be rejected")


def test_diag_callback_ip_is_restricted_to_camera_subnet():
    assert lab.validate_diag_callback_ip("10.0.0.10", "10.0.0.2") == "10.0.0.2"

    for candidate in ("10.0.1.2", "8.8.8.8", "127.0.0.1"):
        try:
            lab.validate_diag_callback_ip("10.0.0.10", candidate)
        except lab.LabError:
            pass
        else:
            raise AssertionError(f"unsafe callback IP accepted: {candidate}")


def test_diagnostic_authcode_is_redacted_from_urls():
    value = redact_secrets(
        "http://10.0.0.10/goform/SingleHandlebyCommand?"
        "singleCMD=SetDiagMode&authcode=123456&userkey=abcdef"
    )
    assert "123456" not in value
    assert "abcdef" not in value
    assert "authcode=<redacted>" in value
