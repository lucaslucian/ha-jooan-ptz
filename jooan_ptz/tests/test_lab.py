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



def test_imaging_ranges_keep_parent_names():
    root = ET.fromstring(
        """
        <Envelope><Body><GetOptionsResponse><ImagingOptions>
          <Brightness><Min>1</Min><Max>255</Max></Brightness>
          <Contrast><Min>2</Min><Max>200</Max></Contrast>
        </ImagingOptions></GetOptionsResponse></Body></Envelope>
        """
    )

    assert lab._extract_ranges(root) == [
        {"name": "Brightness", "min": "1", "max": "255"},
        {"name": "Contrast", "min": "2", "max": "200"},
    ]


def test_notification_parser_keeps_sections_and_motion_state():
    root = ET.fromstring(
        """
        <Envelope><NotificationMessage>
          <Topic>tns1:RuleEngine/CellMotionDetector/Motion</Topic>
          <Message UtcTime="2026-10-03T19:20:00Z" PropertyOperation="Changed">
            <Source><SimpleItem Name="VideoSourceConfigurationToken" Value="VideoSourceToken"/></Source>
            <Data><SimpleItem Name="IsMotion" Value="true"/></Data>
          </Message>
        </NotificationMessage></Envelope>
        """
    )

    message = lab._notification_messages(root)[0]
    assert message["utc_time"] == "2026-10-03T19:20:00Z"
    assert message["property_operation"] == "Changed"
    assert message["source"] == [
        {"name": "VideoSourceConfigurationToken", "value": "VideoSourceToken"}
    ]
    assert message["data"] == [{"name": "IsMotion", "value": "true"}]
    assert message["motion"] is True


def test_storage_discovery_only_uses_advertised_service_paths(monkeypatch):
    info = ptz_info()
    info["device_diagnostics"] = {
        "services_list": {
            "data": [
                {"namespace": lab.ONVIF_RECORDING, "path": "/onvif/Recording"},
                {"namespace": lab.ONVIF_SEARCH, "path": "/onvif/Search"},
                {"namespace": lab.ONVIF_REPLAY, "path": "/onvif/Replay"},
            ]
        }
    }
    calls = []

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        return ok_response(), ET.fromstring("<Envelope/>")

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_storage_discovery(FakeCamera(), info)

    assert result["recording"]["advertised"] is True
    assert result["search"]["advertised"] is True
    assert result["replay"]["advertised"] is True
    assert {call["path"] for call in calls} == {
        "/onvif/Recording",
        "/onvif/Search",
        "/onvif/Replay",
    }
    assert [call["action"] for call in calls] == [
        "GetServiceCapabilities",
        "GetRecordings",
        "GetServiceCapabilities",
        "GetRecordingSummary",
        "GetServiceCapabilities",
        "GetReplayConfiguration",
    ]



def test_event_summary_deduplicates_initialized_snapshots_and_tracks_transitions():
    messages = [
        {
            "topic": "tns1:VideoSource/MotionAlarm",
            "property_operation": "Initialized",
            "source": [{"name": "Source", "value": "VideoSource"}],
            "key": [],
            "data": [{"name": "State", "value": "true"}],
            "motion": True,
            "utc_time": "2026-10-03T19:41:38",
            "pull_index": 0,
        },
        {
            "topic": "tns1:VideoSource/MotionAlarm",
            "property_operation": "Initialized",
            "source": [{"name": "Source", "value": "VideoSource"}],
            "key": [],
            "data": [{"name": "State", "value": "true"}],
            "motion": True,
            "utc_time": "2026-10-03T19:41:40",
            "pull_index": 1,
        },
    ]

    summary = lab._summarize_event_messages(messages)

    assert summary["unique_message_count"] == 1
    assert summary["duplicate_message_count"] == 1
    assert summary["initial_states"] == {"tns1:VideoSource/MotionAlarm": True}
    assert summary["state_changes"] == []
    assert summary["motion_transition_observed"] is False
    assert summary["initialization_only"] is True


def test_event_summary_detects_state_change_even_if_firmware_marks_initialized():
    messages = [
        {
            "topic": "motion",
            "property_operation": "Initialized",
            "source": [],
            "key": [],
            "data": [{"name": "State", "value": "true"}],
            "motion": True,
            "pull_index": 0,
        },
        {
            "topic": "motion",
            "property_operation": "Initialized",
            "source": [],
            "key": [],
            "data": [{"name": "State", "value": "false"}],
            "motion": False,
            "pull_index": 1,
        },
    ]

    summary = lab._summarize_event_messages(messages)

    assert summary["motion_transition_observed"] is True
    assert summary["state_changes"][0]["from"] is True
    assert summary["state_changes"][0]["to"] is False


def test_imaging_write_is_allowlisted_and_bounded(monkeypatch):
    calls = []

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        return ok_response(), ET.fromstring("<Envelope/>")

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_set_imaging(
        FakeCamera(),
        ptz_info(),
        setting="Brightness",
        value=128,
    )

    assert result["accepted"] is True
    assert result["setting"] == "Brightness"
    assert result["value"] == 128
    assert result["force_persistence"] is False
    assert calls[0]["action"] == "SetImagingSettings"
    assert "<tt:Brightness>128</tt:Brightness>" in calls[0]["payload"]
    assert "<timg:ForcePersistence>false</timg:ForcePersistence>" in calls[0]["payload"]


def test_imaging_write_rejects_unknown_fields_and_out_of_range(monkeypatch):
    for setting, value in (("Gain", 128), ("Brightness", 0), ("Sharpness", 256)):
        try:
            lab.onvif_set_imaging(
                FakeCamera(),
                ptz_info(),
                setting=setting,
                value=value,
            )
        except lab.LabError:
            pass
        else:
            raise AssertionError(f"unsafe imaging write accepted: {setting}={value}")



def storage_info():
    info = ptz_info()
    info["device_diagnostics"] = {
        "services_list": {
            "data": [
                {"namespace": lab.ONVIF_RECORDING, "path": "/onvif/DeviceIO"},
                {"namespace": lab.ONVIF_SEARCH, "path": "/onvif/Analytics"},
                {"namespace": lab.ONVIF_REPLAY, "path": "/onvif/Ptz"},
            ]
        }
    }
    return info


def test_recording_playback_probe_uses_only_camera_returned_token(monkeypatch):
    calls = []
    recordings_root = ET.fromstring(
        "<Envelope><RecordingItem><RecordingToken>OnvifRecordingToken_1</RecordingToken></RecordingItem></Envelope>"
    )
    info_root = ET.fromstring(
        "<Envelope><RecordingInformation><RecordingToken>OnvifRecordingToken_1</RecordingToken>"
        "<EarliestRecording>2026-10-01T00:00:00Z</EarliestRecording>"
        "<LatestRecording>2026-10-03T20:00:00Z</LatestRecording></RecordingInformation></Envelope>"
    )
    replay_root = ET.fromstring(
        "<Envelope><GetReplayUriResponse><Uri>rtsp://127.0.0.1:554/replay/stream?token=secret</Uri>"
        "</GetReplayUriResponse></Envelope>"
    )

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        if kwargs["action"] == "GetRecordings":
            return ok_response(), recordings_root
        if kwargs["action"] == "GetRecordingInformation":
            return ok_response(), info_root
        if kwargs["action"] == "GetReplayUri":
            return ok_response(), replay_root
        raise AssertionError(kwargs["action"])

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_recording_playback_probe(FakeCamera(), storage_info())

    assert result["recording_token"] == "OnvifRecordingToken_1"
    assert [call["action"] for call in calls] == [
        "GetRecordings",
        "GetRecordingInformation",
        "GetReplayUri",
    ]
    assert calls[0]["path"] == "/onvif/DeviceIO"
    assert calls[1]["path"] == "/onvif/Analytics"
    assert calls[2]["path"] == "/onvif/Ptz"
    assert "<tse:RecordingToken>OnvifRecordingToken_1</tse:RecordingToken>" in calls[1]["payload"]
    assert "<trp:RecordingToken>OnvifRecordingToken_1</trp:RecordingToken>" in calls[2]["payload"]
    assert result["replay_uri"]["raw_uri_exposed"] is False
    assert result["replay_uri"]["descriptor"]["path"] == "/replay/stream"
    assert result["replay_uri"]["descriptor"]["has_query"] is True
    assert result["replay_uri"]["descriptor"]["query_redacted"] is True


def test_recording_playback_probe_rejects_unreturned_token(monkeypatch):
    recordings_root = ET.fromstring(
        "<Envelope><RecordingItem><RecordingToken>OnvifRecordingToken_1</RecordingToken></RecordingItem></Envelope>"
    )

    def fake_soap(camera, **kwargs):
        assert kwargs["action"] == "GetRecordings"
        return ok_response(), recordings_root

    monkeypatch.setattr(lab, "_soap", fake_soap)
    try:
        lab.onvif_recording_playback_probe(
            FakeCamera(),
            storage_info(),
            recording_token="attacker_token",
        )
    except lab.LabError as exc:
        assert "not returned by the camera" in str(exc)
    else:
        raise AssertionError("unreturned recording token must be rejected")



def test_goto_allows_any_preset_freshly_returned_by_camera(monkeypatch):
    calls = []
    monkeypatch.setattr(
        lab,
        "onvif_list_presets",
        lambda camera, info: {
            "accepted": True,
            "presets": [{"token": "door", "name": "Porta"}],
        },
    )

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        return ok_response(), ET.fromstring("<Envelope/>")

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_goto_preset(FakeCamera(), ptz_info(), token="door")

    assert result["accepted"] is True
    assert result["preset"] == {"token": "door", "name": "Porta"}
    assert calls[0]["action"] == "GotoPreset"
    assert "<tptz:PresetToken>door</tptz:PresetToken>" in calls[0]["payload"]



def test_imaging_path_probe_checks_capability_and_getservices_paths(monkeypatch):
    info = ptz_info()
    info["services"]["imaging"] = {"path": "/onvif/Imaging"}
    info["device_diagnostics"] = {
        "services_list": {
            "data": [
                {"namespace": lab.ONVIF_IMAGING, "path": "/onvif/Recording"},
            ]
        }
    }
    calls = []

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        if kwargs["action"] == "GetOptions":
            root = ET.fromstring(
                "<Envelope><Brightness><Min>1</Min><Max>255</Max></Brightness></Envelope>"
            )
        else:
            root = ET.fromstring("<Envelope/>")
        return ok_response(), root

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_imaging_path_probe(FakeCamera(), info)

    assert result["alternate_path_present"] is True
    assert [item["path"] for item in result["paths"]] == [
        "/onvif/Imaging",
        "/onvif/Recording",
    ]
    assert [call["action"] for call in calls] == [
        "GetServiceCapabilities",
        "GetOptions",
        "GetImagingSettings",
        "GetServiceCapabilities",
        "GetOptions",
        "GetImagingSettings",
    ]
    assert all(call["action"] != "SetImagingSettings" for call in calls)



def test_recording_job_discovery_reads_existing_job_configuration_and_state(monkeypatch):
    calls = []
    jobs_root = ET.fromstring(
        "<Envelope><JobItem><JobToken>job_1</JobToken><JobConfiguration>"
        "<RecordingToken>OnvifRecordingToken_1</RecordingToken><Mode>Idle</Mode>"
        "<Priority>1</Priority></JobConfiguration></JobItem></Envelope>"
    )

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        action = kwargs["action"]
        if action == "GetRecordingJobs":
            return ok_response(), jobs_root
        if action == "GetRecordingJobConfiguration":
            return ok_response(), ET.fromstring(
                "<Envelope><JobConfiguration><RecordingToken>OnvifRecordingToken_1</RecordingToken>"
                "<Mode>Idle</Mode><Priority>1</Priority></JobConfiguration></Envelope>"
            )
        if action == "GetRecordingJobState":
            return ok_response(), ET.fromstring(
                "<Envelope><State><RecordingToken>OnvifRecordingToken_1</RecordingToken>"
                "<State>Idle</State></State></Envelope>"
            )
        raise AssertionError(action)

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_recording_job_discovery(FakeCamera(), storage_info())

    assert result["job_count"] == 1
    assert result["jobs"] == [{
        "token": "job_1",
        "mode": "Idle",
        "recording_token": "OnvifRecordingToken_1",
        "priority": "1",
    }]
    assert [call["action"] for call in calls] == [
        "GetRecordingJobs",
        "GetRecordingJobConfiguration",
        "GetRecordingJobState",
    ]


def test_recording_pulse_only_toggles_existing_idle_job_and_restores(monkeypatch):
    discovery = {
        "jobs": [{
            "token": "job_1",
            "mode": "Idle",
            "recording_token": "OnvifRecordingToken_1",
            "priority": "1",
        }]
    }
    calls = []

    monkeypatch.setattr(lab, "onvif_recording_job_discovery", lambda camera, info: discovery)
    monkeypatch.setattr(
        lab,
        "_recording_info_by_token",
        lambda camera, info, token: {"accepted": True, "recording_status": "stub"},
    )
    monkeypatch.setattr(
        lab,
        "_recording_job_read",
        lambda camera, **kwargs: {"accepted": True, "values": [{"name": "State", "value": "Active"}]},
    )
    monkeypatch.setattr(lab.time, "sleep", lambda _: None)

    def fake_soap(camera, **kwargs):
        calls.append(kwargs)
        assert kwargs["action"] == "SetRecordingJobMode"
        return ok_response(), ET.fromstring("<Envelope/>")

    monkeypatch.setattr(lab, "_soap", fake_soap)
    result = lab.onvif_recording_pulse(FakeCamera(), storage_info(), seconds=9)

    assert result["seconds"] == 5
    assert result["executed"] is True
    assert result["created_objects"] is False
    assert result["deleted_objects"] is False
    assert result["job_configuration_changed"] is False
    assert len(calls) == 2
    assert "<trc:Mode>Active</trc:Mode>" in calls[0]["payload"]
    assert "<trc:Mode>Idle</trc:Mode>" in calls[1]["payload"]


def test_recording_pulse_refuses_non_idle_job_without_write(monkeypatch):
    monkeypatch.setattr(
        lab,
        "onvif_recording_job_discovery",
        lambda camera, info: {
            "jobs": [{
                "token": "job_1",
                "mode": "Active",
                "recording_token": "OnvifRecordingToken_1",
                "priority": "1",
            }]
        },
    )
    monkeypatch.setattr(
        lab,
        "_soap",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not write")),
    )

    result = lab.onvif_recording_pulse(FakeCamera(), storage_info(), seconds=5)

    assert result["executed"] is False
    assert "not Idle" in result["reason"]
