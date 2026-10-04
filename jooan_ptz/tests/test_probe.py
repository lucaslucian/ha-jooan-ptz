import xml.etree.ElementTree as ET

import camera as camera_module
import probe as probe_module
from camera import JooanCamera
from probe import (
    _extract_capability_services,
    _extract_device_information,
    _extract_network_interfaces,
    _extract_presets,
    _extract_ptz_nodes,
    _extract_profiles,
    _extract_stream_uri,
    _safe_rtsp_descriptor,
    _soap_fault,
    onvif_ptz_discovery,
)


def test_safe_rtsp_descriptor_strips_credentials_and_host():
    value = _safe_rtsp_descriptor(
        "rtsp://admin:secret@127.0.0.1:8554/live/ch00_0"
    )
    assert value == {
        "path": "/live/ch00_0",
        "reported_port": 8554,
        "reported_host_is_loopback": True,
    }


def test_extract_onvif_capability_service_paths():
    root = ET.fromstring(
        """<?xml version="1.0"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                    xmlns:tt="http://www.onvif.org/ver10/schema">
          <s:Body>
            <GetCapabilitiesResponse>
              <tt:Capabilities>
                <tt:Device>
                  <tt:XAddr>http://127.0.0.1:8899/onvif/device_service</tt:XAddr>
                </tt:Device>
                <tt:Media>
                  <tt:XAddr>http://127.0.0.1:8899/onvif/Media</tt:XAddr>
                </tt:Media>
                <tt:PTZ>
                  <tt:XAddr>http://127.0.0.1:8899/onvif/Ptz</tt:XAddr>
                </tt:PTZ>
              </tt:Capabilities>
            </GetCapabilitiesResponse>
          </s:Body>
        </s:Envelope>
        """
    )
    services = _extract_capability_services(root)
    assert services["device"]["path"] == "/onvif/device_service"
    assert services["media"]["path"] == "/onvif/Media"
    assert services["ptz"]["path"] == "/onvif/Ptz"
    assert services["media"]["reported_port"] == 8899


def test_extract_profiles_and_stream_uri():
    profiles_root = ET.fromstring(
        """<?xml version="1.0"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                    xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
                    xmlns:tt="http://www.onvif.org/ver10/schema">
          <s:Body>
            <trt:GetProfilesResponse>
              <trt:Profiles token="profile_0">
                <tt:Name>Main</tt:Name>
                <tt:VideoEncoderConfiguration>
                  <tt:Encoding>H264</tt:Encoding>
                  <tt:Resolution>
                    <tt:Width>2304</tt:Width>
                    <tt:Height>1296</tt:Height>
                  </tt:Resolution>
                  <tt:RateControl>
                    <tt:FrameRateLimit>15</tt:FrameRateLimit>
                    <tt:BitrateLimit>2048</tt:BitrateLimit>
                  </tt:RateControl>
                </tt:VideoEncoderConfiguration>
                <tt:AudioEncoderConfiguration>
                  <tt:Encoding>G711</tt:Encoding>
                  <tt:Bitrate>64</tt:Bitrate>
                  <tt:SampleRate>8</tt:SampleRate>
                </tt:AudioEncoderConfiguration>
              </trt:Profiles>
            </trt:GetProfilesResponse>
          </s:Body>
        </s:Envelope>
        """
    )
    profiles = _extract_profiles(profiles_root)
    assert profiles == [
        {
            "token": "profile_0",
            "name": "Main",
            "video": {
                "encoding": "H264",
                "width": 2304,
                "height": 1296,
                "frame_rate_limit": 15,
                "bitrate_limit_kbps": 2048,
            },
            "audio": {
                "encoding": "G711",
                "bitrate_kbps": 64,
                "sample_rate_khz": 8,
            },
        }
    ]

    stream_root = ET.fromstring(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                       xmlns:tt="http://www.onvif.org/ver10/schema">
          <s:Body>
            <GetStreamUriResponse>
              <MediaUri>
                <tt:Uri>rtsp://127.0.0.1:554/live/ch00_0</tt:Uri>
              </MediaUri>
            </GetStreamUriResponse>
          </s:Body>
        </s:Envelope>"""
    )
    assert _extract_stream_uri(stream_root)["path"] == "/live/ch00_0"


def test_extract_presets():
    root = ET.fromstring(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                       xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
                       xmlns:tt="http://www.onvif.org/ver10/schema">
          <s:Body>
            <tptz:GetPresetsResponse>
              <tptz:Preset token="1"><tt:Name>Porta</tt:Name></tptz:Preset>
              <tptz:Preset token="2"><tt:Name>Pátio</tt:Name></tptz:Preset>
            </tptz:GetPresetsResponse>
          </s:Body>
        </s:Envelope>"""
    )
    assert _extract_presets(root) == [
        {"token": "1", "name": "Porta"},
        {"token": "2", "name": "Pátio"},
    ]


def test_rtsp_probe_runs_sequentially(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "http-password")
    monkeypatch.setattr(
        camera,
        "get_rtsp_credentials",
        lambda: ("admin", "rtsp-password"),
    )

    calls = []

    def fake_ffprobe(url, timeout=None):
        calls.append(url)
        return {"available": True, "error": None, "streams": [{"codec_type": "video"}]}

    monkeypatch.setattr(camera_module, "ffprobe_rtsp", fake_ffprobe)
    monkeypatch.setattr(camera_module.time, "sleep", lambda _: None)
    result = camera.probe_rtsp_streams(
        {
            "profiles": [
                {"stream": {"path": "/media/live/profile0"}},
            ]
        }
    )

    assert result["probe_mode"] == "sequential-minimal"
    assert result["reachable"] is True
    assert [item["path"] for item in result["streams"]] == [
        "/live/ch00_0",
        "/live/ch01_0",
    ]
    assert len(calls) == 2


def test_extract_extended_onvif_diagnostics():
    root = ET.fromstring(
        """<Envelope>
          <Manufacturer>JOOAN</Manufacturer>
          <Model>JA-A12</Model>
          <FirmwareVersion>1.2.3</FirmwareVersion>
          <SerialNumber>serial</SerialNumber>
          <HardwareId>hw</HardwareId>
          <NetworkInterfaces token="eth0">
            <Enabled>true</Enabled>
            <Info><Name>eth0</Name><HwAddress>AA:BB:CC:DD:EE:FF</HwAddress><MTU>1500</MTU></Info>
            <IPv4><Config><Manual><Address>10.0.0.10</Address><PrefixLength>24</PrefixLength></Manual></Config></IPv4>
          </NetworkInterfaces>
          <PTZNode token="node0">
            <Name>PTZ</Name>
            <HomeSupported>true</HomeSupported>
            <MaximumNumberOfPresets>8</MaximumNumberOfPresets>
          </PTZNode>
        </Envelope>"""
    )
    assert _extract_device_information(root)["model"] == "JA-A12"
    interfaces = _extract_network_interfaces(root)
    assert interfaces[0]["token"] == "eth0"
    assert interfaces[0]["ipv4"][0]["address"] == "10.0.0.10"
    nodes = _extract_ptz_nodes(root)
    assert nodes[0]["home_supported"] == "true"
    assert nodes[0]["maximum_presets"] == "8"


def test_rtsp_probe_uses_substream_only_when_main_fails(monkeypatch):
    camera = JooanCamera("10.0.0.10", "admin", "http-password")
    monkeypatch.setattr(camera, "get_rtsp_credentials", lambda: ("admin", "rtsp-password"))
    monkeypatch.setattr(camera_module.time, "sleep", lambda _: None)

    calls = []

    def fake_ffprobe(url, timeout=None):
        calls.append(url)
        available = "/live/ch00_1" in url
        return {
            "available": available,
            "error": None if available else "missing",
            "streams": [{"codec_type": "video"}] if available else [],
        }

    monkeypatch.setattr(camera_module, "ffprobe_rtsp", fake_ffprobe)
    result = camera.probe_rtsp_streams(channel_count=1)

    assert [item["path"] for item in result["streams"]] == [
        "/live/ch00_0",
        "/live/ch00_1",
    ]
    assert result["reachable"] is True


def test_extract_profiles_ignores_malformed_numeric_values():
    root = ET.fromstring(
        """<Envelope>
          <Profiles token="profile_bad">
            <VideoEncoderConfiguration>
              <Encoding>H264</Encoding>
              <Resolution><Width>not-a-number</Width><Height>1296</Height></Resolution>
              <RateControl><FrameRateLimit>bad</FrameRateLimit></RateControl>
            </VideoEncoderConfiguration>
          </Profiles>
        </Envelope>"""
    )
    profiles = _extract_profiles(root)
    assert profiles[0]["video"]["encoding"] == "H264"
    assert "width" not in profiles[0]["video"]
    assert "frame_rate_limit" not in profiles[0]["video"]
    assert profiles[0]["video"]["height"] == 1296


def test_soap_fault_ignores_non_fault_reason_text():
    root = ET.fromstring("<Envelope><Reason>normal metadata</Reason></Envelope>")
    assert _soap_fault(root) is None


def test_soap_fault_reads_actual_fault_text():
    root = ET.fromstring(
        """<Envelope>
          <Fault>
            <Reason><Text>Unsupported operation</Text></Reason>
          </Fault>
        </Envelope>"""
    )
    assert _soap_fault(root) == "Unsupported operation"


def test_profile_extraction_is_bounded():
    xml = "<Envelope>" + "".join(
        f'<Profiles token="p{i}"><Name>P{i}</Name></Profiles>' for i in range(20)
    ) + "</Envelope>"
    profiles = _extract_profiles(ET.fromstring(xml))
    assert len(profiles) == 4


def test_malformed_capability_xaddr_is_ignored():
    root = ET.fromstring(
        """<Envelope>
          <Capabilities>
            <Media><XAddr>http://[broken/onvif/Media</XAddr></Media>
            <PTZ><XAddr>http://127.0.0.1:8899/onvif/Ptz</XAddr></PTZ>
          </Capabilities>
        </Envelope>"""
    )
    services = _extract_capability_services(root)
    assert "media" not in services
    assert services["ptz"]["path"] == "/onvif/Ptz"


def test_minimal_onvif_ptz_discovery_uses_only_capabilities_and_profiles(monkeypatch):
    calls = []
    capabilities = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
      xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><GetCapabilitiesResponse>
      <tt:Capabilities>
        <tt:Media><tt:XAddr>http://127.0.0.1:8899/onvif/Media</tt:XAddr></tt:Media>
        <tt:PTZ><tt:XAddr>http://127.0.0.1:8899/onvif/Ptz</tt:XAddr></tt:PTZ>
      </tt:Capabilities></GetCapabilitiesResponse></s:Body></s:Envelope>"""
    profiles = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
      xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
      xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><trt:GetProfilesResponse>
      <trt:Profiles token="profile_0"><tt:Name>Main</tt:Name></trt:Profiles>
      </trt:GetProfilesResponse></s:Body></s:Envelope>"""

    responses = [
        {"status": 200, "body": capabilities, "error": None, "authentication_required": False},
        {"status": 200, "body": profiles, "error": None, "authentication_required": False},
    ]

    def fake_soap_post(host, port, path, body, *, action=None, timeout=3.0):
        calls.append((host, port, path, action))
        return responses[len(calls) - 1]

    monkeypatch.setattr(probe_module, "_soap_post", fake_soap_post)

    result = onvif_ptz_discovery("10.0.0.10", 8899)

    assert result["probe_mode"] == "ptz-minimal"
    assert result["reachable"] is True
    assert result["ptz"] == {
        "path": "/onvif/Ptz",
        "profile_token": "profile_0",
    }
    assert len(calls) == 2
    assert calls[0][2] == "/onvif/device_service"
    assert calls[1][2] == "/onvif/Media"
