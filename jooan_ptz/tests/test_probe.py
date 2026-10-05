import subprocess
import xml.etree.ElementTree as ET

import probe as probe_module
from probe import (
    _extract_capability_services,
    _extract_profiles,
    _soap_fault,
    capture_snapshot,
    onvif_continuous_move,
    onvif_ptz_discovery,
)


def test_extract_onvif_capability_service_paths():
    root = ET.fromstring(
        """<Envelope>
          <Capabilities>
            <Media><XAddr>http://127.0.0.1:8899/onvif/Media</XAddr></Media>
            <PTZ><XAddr>http://127.0.0.1:8899/onvif/Ptz</XAddr></PTZ>
          </Capabilities>
        </Envelope>"""
    )
    services = _extract_capability_services(root)
    assert services["media"]["path"] == "/onvif/Media"
    assert services["ptz"]["path"] == "/onvif/Ptz"
    assert services["media"]["reported_port"] == 8899


def test_extract_profiles_is_bounded_and_tolerates_bad_numbers():
    xml = "<Envelope>" + "".join(
        (
            f'<Profiles token="p{i}"><Name>P{i}</Name>'
            '<VideoEncoderConfiguration><Encoding>H264</Encoding>'
            '<Resolution><Width>bad</Width><Height>1296</Height></Resolution>'
            '</VideoEncoderConfiguration></Profiles>'
        )
        for i in range(10)
    ) + "</Envelope>"
    profiles = _extract_profiles(ET.fromstring(xml))
    assert len(profiles) == 4
    assert profiles[0]["video"]["encoding"] == "H264"
    assert profiles[0]["video"]["height"] == 1296
    assert "width" not in profiles[0]["video"]


def test_soap_fault_reads_only_actual_fault():
    normal = ET.fromstring("<Envelope><Reason>metadata</Reason></Envelope>")
    fault = ET.fromstring(
        "<Envelope><Fault><Reason><Text>Unsupported operation</Text></Reason></Fault></Envelope>"
    )
    assert _soap_fault(normal) is None
    assert _soap_fault(fault) == "Unsupported operation"


def test_minimal_onvif_ptz_discovery_uses_capabilities_and_profiles(monkeypatch):
    calls = []
    capabilities = b"""<Envelope><Capabilities>
      <Media><XAddr>http://127.0.0.1:8899/onvif/Media</XAddr></Media>
      <PTZ><XAddr>http://127.0.0.1:8899/onvif/Ptz</XAddr></PTZ>
    </Capabilities></Envelope>"""
    profiles = b"""<Envelope><Profiles token="profile_0"><Name>Main</Name></Profiles></Envelope>"""
    responses = [
        {"status": 200, "body": capabilities, "error": None, "authentication_required": False},
        {"status": 200, "body": profiles, "error": None, "authentication_required": False},
    ]

    def fake_soap_post(host, port, path, body, *, action=None, timeout=3.0):
        calls.append((host, port, path, action))
        return responses[len(calls) - 1]

    monkeypatch.setattr(probe_module, "_soap_post", fake_soap_post)
    result = onvif_ptz_discovery("10.0.0.10", 8899)

    assert result["reachable"] is True
    assert result["probe_mode"] == "ptz-minimal"
    assert result["ptz"] == {"path": "/onvif/Ptz", "profile_token": "profile_0"}
    assert [call[2] for call in calls] == ["/onvif/device_service", "/onvif/Media"]


def test_onvif_continuous_move_always_sends_stop(monkeypatch):
    calls = []

    def fake_soap_post(host, port, path, body, *, action=None, timeout=3.0):
        calls.append((path, action, body))
        return {"status": 200, "body": b"<Envelope/>", "error": None, "authentication_required": False}

    monkeypatch.setattr(probe_module, "_soap_post", fake_soap_post)
    monkeypatch.setattr(probe_module.time, "sleep", lambda _seconds: None)

    result = onvif_continuous_move(
        "10.0.0.10",
        8899,
        {"ptz": {"path": "/onvif/Ptz", "profile_token": "profile_0"}},
        direction="left",
        speed=0.4,
        duration_ms=250,
    )

    assert result["start"]["accepted"] is True
    assert result["stop"]["accepted"] is True
    assert len(calls) == 2
    assert calls[0][1].endswith("/ContinuousMove")
    assert calls[1][1].endswith("/Stop")


def test_capture_snapshot_uses_single_ffmpeg_frame(monkeypatch):
    seen = {}

    def fake_run(command, timeout):
        seen["command"] = command
        seen["timeout"] = timeout
        return subprocess.CompletedProcess(command, 0, stdout=b"jpeg", stderr=b"")

    monkeypatch.setattr(probe_module, "_run", fake_run)
    assert capture_snapshot("rtsp://camera/live/ch00_0") == b"jpeg"
    assert "-frames:v" in seen["command"]
    assert "1" in seen["command"]
