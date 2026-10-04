import io
import threading
import time

import main


def setup_function():
    main._stop_live_preview()
    main._snapshot_cache.clear()
    main._ptz_sequences.clear()
    main._preview_processes.clear()
    main._preview_generations.update({0: 0, 1: 0})
    main._ptz_onvif_disabled_until = 0.0
    main.update_state(
        online=False,
        authenticated=False,
        probe_running=False,
        ptz_moving=False,
        ptz_channel=None,
        ptz_channel_source=None,
        ptz_onvif_available=False,
        ptz_last_transport=None,
        ptz_last_error=None,
        preview_active=False,
        preview_channel=None,
        preview_stream=None,
        preview_source=None,
        preview_channels=[],
        preview_streams={},
        preview_probe=None,
        preview_probe_running=False,
        preview_last_error=None,
        last_error=None,
    )


def test_last_good_snapshot_is_returned_after_transient_failure(monkeypatch):
    calls = {"count": 0}

    class FakeCamera:
        def snapshot(self, stream):
            calls["count"] += 1
            if calls["count"] == 1:
                return b"jpeg-first-good-frame"
            raise RuntimeError("transient RTSP failure")

    camera = FakeCamera()
    monkeypatch.setattr(main, "get_camera", lambda: camera)
    monkeypatch.setattr(
        main,
        "_snapshot_cache_key",
        lambda stream: ("10.0.0.10", 554, stream),
    )

    first, first_stale = main._capture_snapshot_with_fallback("ch00_0")
    second, second_stale = main._capture_snapshot_with_fallback("ch00_0")

    assert first == b"jpeg-first-good-frame"
    assert first_stale is False
    assert second == first
    assert second_stale is True


def test_snapshot_capture_is_serialized(monkeypatch):
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    class FakeCamera:
        def snapshot(self, stream):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return f"jpeg-{stream}".encode()

    camera = FakeCamera()
    monkeypatch.setattr(main, "get_camera", lambda: camera)
    monkeypatch.setattr(
        main,
        "_snapshot_cache_key",
        lambda stream: ("10.0.0.10", 554, stream),
    )

    results = []

    def worker(stream):
        results.append(main._capture_snapshot_with_fallback(stream)[0])

    threads = [
        threading.Thread(target=worker, args=("ch00_0",)),
        threading.Thread(target=worker, args=("ch01_0",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1
    assert sorted(results) == [b"jpeg-ch00_0", b"jpeg-ch01_0"]


def test_expired_snapshot_cache_is_not_returned(monkeypatch):
    key = ("10.0.0.10", 554, "ch00_0")
    main._snapshot_cache[key] = (
        time.monotonic() - main.SNAPSHOT_CACHE_TTL - 1,
        b"too-old",
    )

    class FakeCamera:
        def snapshot(self, stream):
            raise RuntimeError("capture failed")

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    monkeypatch.setattr(main, "_snapshot_cache_key", lambda stream: key)

    try:
        main._capture_snapshot_with_fallback("ch00_0")
    except RuntimeError as exc:
        assert "capture failed" in str(exc)
    else:
        raise AssertionError("expired cache must not be returned")


def test_snapshot_uses_stale_cache_while_camera_io_is_busy(monkeypatch):
    key = ("10.0.0.10", 554, "ch00_0")
    main._snapshot_cache[key] = (time.monotonic(), b"cached-frame")
    monkeypatch.setattr(main, "_snapshot_cache_key", lambda stream: key)
    monkeypatch.setattr(main, "CAMERA_IO_LOCK_TIMEOUT", 0.01)

    main._camera_io_lock.acquire()
    try:
        image, stale = main._capture_snapshot_with_fallback("ch00_0")
    finally:
        main._camera_io_lock.release()

    assert image == b"cached-frame"
    assert stale is True


def test_stale_ptz_sequence_is_ignored(monkeypatch):
    commands = []

    class FakeCamera:
        ip = "10.0.0.10"
        onvif_port = 8899

        def command(self, direction):
            commands.append(direction)
            return {"result": "success"}

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    main.update_state(online=True, authenticated=True)
    client = main.app.test_client()

    newer = client.post("/api/ptz/stop?client=test-client&seq=2")
    older = client.post("/api/ptz/right?client=test-client&seq=1")

    assert newer.status_code == 200
    assert older.status_code == 200
    assert older.get_json()["ignored"] is True
    assert commands == ["stop"]


def test_ptz_prefers_onvif_and_passes_selected_speed(monkeypatch):
    calls = []

    class FakeCamera:
        ip = "10.0.0.10"
        onvif_port = 8899

        def command(self, direction):
            raise AssertionError("CGI fallback must not run when ONVIF succeeds")

    def fake_onvif(host, port, onvif_info, *, direction, speed, duration_ms):
        calls.append((host, port, direction, speed, duration_ms))
        return {
            "start": {"accepted": True},
            "stop": {"accepted": True},
        }

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    monkeypatch.setattr(main, "onvif_continuous_move", fake_onvif)
    main.update_state(
        online=True,
        authenticated=True,
        ptz_onvif_available=True,
        onvif_info={"ptz": {"profile_token": "profile_0"}},
    )
    client = main.app.test_client()

    response = client.post(
        "/api/ptz/right?client=test-client&seq=1&speed=0.8&duration_ms=320"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["transport"] == "onvif"
    assert payload["speed"] == 0.8
    assert calls == [("10.0.0.10", 8899, "right", 0.8, 320)]


def test_ptz_falls_back_to_cgi_when_onvif_is_rejected(monkeypatch):
    commands = []

    class FakeCamera:
        ip = "10.0.0.10"
        onvif_port = 8899

        def command(self, direction):
            commands.append(direction)
            return {"result": "success"}

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    monkeypatch.setattr(
        main,
        "onvif_continuous_move",
        lambda *args, **kwargs: {
            "start": {"accepted": False, "fault": "ActionNotSupported"},
            "stop": {"accepted": True},
        },
    )
    main.update_state(
        online=True,
        authenticated=True,
        ptz_onvif_available=True,
        onvif_info={"ptz": {"profile_token": "profile_0"}},
    )
    client = main.app.test_client()

    response = client.post(
        "/api/ptz/left?client=test-client&seq=1&speed=0.6&duration_ms=280"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["transport"] == "cgi-fallback"
    assert commands == ["left"]
    assert "ActionNotSupported" in payload["onvif_error"]


def test_invalid_ptz_direction_is_rejected_without_camera_call(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_camera",
        lambda: (_ for _ in ()).throw(AssertionError("camera must not be called")),
    )
    main.update_state(online=True, authenticated=True)
    client = main.app.test_client()
    response = client.post("/api/ptz/SetDiagMode?client=test-client&seq=1")

    assert response.status_code == 400


def test_offline_heartbeat_clears_authenticated_state(monkeypatch):
    class FakeCamera:
        def heartbeat(self):
            return {"online": False, "method": "icmp", "error": "ICMP ping failed"}

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    main.update_state(online=True, authenticated=True)

    assert main.heartbeat_camera() is False
    with main._state_lock:
        assert main._state["online"] is False
        assert main._state["authenticated"] is False


def test_api_responses_disable_caching():
    client = main.app.test_client()
    response = client.get("/api/status")

    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_light_test_rejects_while_probe_is_running(monkeypatch):
    monkeypatch.setattr(
        main,
        "validate_camera",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("validation must not run while deep probe is active")
        ),
    )
    main.update_state(probe_running=True)
    client = main.app.test_client()

    response = client.post("/api/test")

    assert response.status_code == 409
    assert response.get_json()["busy"] is True


def test_unavailable_icmp_preserves_last_known_online_state(monkeypatch):
    class FakeCamera:
        def heartbeat(self):
            return {
                "online": None,
                "method": "icmp",
                "error": "ICMP heartbeat is unavailable in this container",
            }

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    main.update_state(online=True, authenticated=True)

    assert main.heartbeat_camera() is True
    with main._state_lock:
        assert main._state["online"] is True
        assert main._state["authenticated"] is True
        assert main._state["heartbeat_error"] is not None


def test_deep_probe_is_rejected_while_ptz_is_moving():
    main.update_state(ptz_moving=True, probe_running=False)
    client = main.app.test_client()

    response = client.post("/api/probe")

    assert response.status_code == 409
    assert "Stop PTZ" in response.get_json()["error"]


def test_snapshot_does_not_open_rtsp_while_ptz_is_moving(monkeypatch):
    key = ("10.0.0.10", 554, "ch00_0")
    main._snapshot_cache[key] = (time.monotonic(), b"cached-frame")
    monkeypatch.setattr(main, "_snapshot_cache_key", lambda stream: key)

    class FakeCamera:
        def snapshot(self, stream):
            raise AssertionError("RTSP must not open while PTZ is moving")

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    main.update_state(ptz_moving=True)

    image, stale = main._capture_snapshot_with_fallback("ch00_0")

    assert image == b"cached-frame"
    assert stale is True


def test_light_test_rejects_while_ptz_is_moving(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_camera_locked",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("validation must not run while PTZ is moving")
        ),
    )
    main.update_state(ptz_moving=True, probe_running=False)
    client = main.app.test_client()

    response = client.post("/api/test")

    assert response.status_code == 409
    assert response.get_json()["busy"] is True


def test_dashboard_template_is_served():
    client = main.app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "CÂMERAS & PTZ" in body
    assert "liveFeed0" in body
    assert "liveFeed1" in body
    assert "ptzSpeed" in body
    assert "generalInfo" in body
    assert "allReadSettings" in body
    assert 'class="tabs"' not in body
    assert "Laboratório" not in body
    assert "labCgiProbeAll" not in body
    assert "static/app.css" in body
    assert "static/app.js" in body


def test_preview_prefers_onvif_substream():
    main.update_state(
        onvif_info={
            "profiles": [
                {
                    "name": "SubStream",
                    "stream": {"path": "/live/ch00_1"},
                    "video": {"width": 640, "height": 360},
                }
            ]
        },
        media_probe={
            "streams": [
                {
                    "path": "/live/ch00_0",
                    "available": True,
                    "streams": [{"codec_type": "video"}],
                }
            ]
        },
    )

    assert main._select_preview_stream(0) == ("/live/ch00_1", "onvif_substream")


def test_preview_prefers_validated_substream_over_onvif():
    main.update_state(
        preview_probe={
            "streams": [
                {
                    "path": "/live/ch01_1",
                    "available": True,
                    "streams": [{"codec_type": "video"}],
                }
            ]
        },
        onvif_info={"profiles": []},
        media_probe={"streams": []},
    )

    assert main._select_preview_stream(1) == (
        "/live/ch01_1",
        "validated_substream",
    )


def test_live_preview_streams_mjpeg_without_exposing_rtsp(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.stdout = io.BytesIO(b"TAIL")
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    process = FakeProcess()
    first = b"--jooanframe\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n"
    monkeypatch.setattr(
        main,
        "_start_preview_process",
        lambda channel: (
            process,
            first,
            "/live/ch00_1",
            "onvif_substream",
        ),
    )
    main.update_state(
        online=True,
        authenticated=True,
        probe_running=False,
        preview_probe_running=False,
        ptz_channel=0,
    )

    client = main.app.test_client()
    response = client.get("/api/live/ptz")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith(
        "multipart/x-mixed-replace"
    )
    assert response.headers["X-JOOAN-Preview-Stream"] == "/live/ch00_1"
    assert b"JPEG" in response.data
    assert "rtsp://" not in response.get_data(as_text=True)


def test_stopping_one_live_preview_keeps_the_other_running():
    class FakeProcess:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    first = FakeProcess()
    second = FakeProcess()
    main._preview_processes[0] = first
    main._preview_processes[1] = second
    main.update_state(
        preview_active=True,
        preview_channels=[0, 1],
        preview_streams={
            "0": {"path": "/live/ch00_1", "source": "test"},
            "1": {"path": "/live/ch01_1", "source": "test"},
        },
    )

    assert main._stop_live_preview(0) is True
    assert first.terminated is True
    assert second.terminated is False
    assert sorted(main._preview_processes) == [1]
    with main._state_lock:
        assert main._state["preview_active"] is True
        assert main._state["preview_channels"] == [1]


def test_snapshot_uses_cache_while_live_preview_is_active(monkeypatch):
    key = ("10.0.0.10", 554, "ch00_0")
    main._snapshot_cache[key] = (time.monotonic(), b"cached-live-frame")
    monkeypatch.setattr(main, "_snapshot_cache_key", lambda stream: key)
    main.update_state(preview_active=True, ptz_moving=False)

    class FakeCamera:
        def snapshot(self, stream):
            raise AssertionError("snapshot RTSP must not open during live preview")

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    image, stale = main._capture_snapshot_with_fallback("ch00_0")

    assert image == b"cached-live-frame"
    assert stale is True


def test_deep_probe_is_rejected_while_live_preview_is_active():
    main.update_state(preview_active=True, ptz_moving=False, probe_running=False)
    client = main.app.test_client()

    response = client.post("/api/probe")

    assert response.status_code == 409
    assert "preview" in response.get_json()["error"].lower()


def test_ptz_channel_is_inferred_from_onvif_profile_mapping():
    channel, source = main._infer_ptz_channel(
        {
            "profiles": [
                {
                    "token": "profile_0",
                    "stream": {"path": "/live/ch00_0"},
                },
                {
                    "token": "profile_1",
                    "stream": {"path": "/live/ch00_1"},
                },
            ],
            "ptz": {"profile_token": "profile_0"},
        }
    )

    assert channel == 0
    assert source == "onvif_profile_mapping"


def test_preview_candidates_fall_back_from_substream_to_main():
    main.update_state(
        preview_probe={
            "streams": [
                {
                    "path": "/live/ch00_1",
                    "available": True,
                    "streams": [{"codec_type": "video"}],
                }
            ]
        },
        onvif_info={"profiles": []},
        media_probe={
            "streams": [
                {
                    "path": "/live/ch00_0",
                    "available": True,
                    "streams": [{"codec_type": "video"}],
                }
            ]
        },
    )

    assert main._preview_candidates(0) == [
        ("/live/ch00_1", "validated_substream"),
        ("/live/ch00_0", "confirmed_main"),
    ]




def test_laboratory_routes_are_removed():
    client = main.app.test_client()

    assert client.post("/api/lab/cgi/probe").status_code == 404
    assert client.post("/api/lab/onvif/ptz").status_code == 404
    assert client.post("/api/lab/diag/probe").status_code == 404
