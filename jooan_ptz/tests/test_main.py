import threading
import time
from pathlib import Path

import main


def setup_function():
    main._snapshot_cache.clear()
    main._ptz_sequences.clear()
    main._ptz_onvif_disabled_until = 0.0
    main._ptz_onvif_discovery_attempted = False
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


def test_ptz_lazy_discovers_onvif_once(monkeypatch):
    discoveries = []
    moves = []

    class FakeCamera:
        ip = "10.0.0.10"
        onvif_port = 8899

        def command(self, direction):
            raise AssertionError("CGI fallback must not run when ONVIF discovery succeeds")

    def fake_discovery(host, port):
        discoveries.append((host, port))
        return {
            "reachable": True,
            "ptz": {"path": "/onvif/Ptz", "profile_token": "profile_0"},
            "profiles": [{"token": "profile_0", "name": "Main"}],
        }

    def fake_move(host, port, onvif_info, *, direction, speed, duration_ms):
        moves.append(direction)
        return {"start": {"accepted": True}, "stop": {"accepted": True}}

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    monkeypatch.setattr(main, "onvif_ptz_discovery", fake_discovery)
    monkeypatch.setattr(main, "onvif_continuous_move", fake_move)
    main.update_state(
        online=True,
        authenticated=True,
        ptz_onvif_available=False,
        onvif_info=None,
        services={},
    )
    client = main.app.test_client()

    first = client.post("/api/ptz/right?client=lazy&seq=1&speed=0.4")
    second = client.post("/api/ptz/left?client=lazy&seq=2&speed=0.4")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["transport"] == "onvif"
    assert second.get_json()["transport"] == "onvif"
    assert discoveries == [("10.0.0.10", 8899)]
    assert moves == ["right", "left"]


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
    response = client.post("/api/ptz/zoom?client=test-client&seq=1")

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


def test_ptz_preview_snapshot_does_not_block_on_camera_io_lock(monkeypatch):
    key = ("10.0.0.10", 554, "ch00_0")
    monkeypatch.setattr(main, "_snapshot_cache_key", lambda stream: key)

    class FakeCamera:
        def snapshot(self, stream):
            assert stream == "ch00_0"
            return b"jpeg-while-ptz-moves"

    monkeypatch.setattr(main, "get_camera", lambda: FakeCamera())
    main.update_state(ptz_moving=True)

    # Simulate another PTZ/ONVIF operation owning the control-plane lock.
    # A PTZ preview frame must still be allowed so its FFmpeg process cannot
    # become the reason a future STOP waits on that lock.
    main._camera_io_lock.acquire()
    try:
        image, stale = main._capture_snapshot_with_fallback(
            "ch00_0",
            ptz_preview=True,
        )
    finally:
        main._camera_io_lock.release()

    assert image == b"jpeg-while-ptz-moves"
    assert stale is False


def test_dashboard_template_is_served():
    client = main.app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "CÂMERAS & PTZ" in body
    assert "snapshotFeed0" in body
    assert "snapshotFeed1" in body
    assert "Preview PTZ · 1 FPS" in body
    assert "ptzSpeed" in body
    assert "generalInfo" in body
    assert 'class="info-accordion-item camera-info-collapse"' in body
    assert '<strong>Informações gerais</strong>' in body
    assert 'class="card camera-info-card"' not in body
    assert 'id="feedTile0"' in body
    assert 'id="feedTile1"' in body
    assert 'id="feedPtzMarker0"' in body
    assert 'id="feedPtzMarker1"' in body
    assert 'class="control-bottom"' not in body
    assert "allReadSettings" in body
    assert 'id="headerTitle"' in body
    assert 'class="control-side"' not in body
    assert 'id="statusBanner"' in body
    assert 'aria-live="polite"' in body
    assert 'class="info-accordion"' in body
    assert body.count('class="info-accordion-item"') == 4
    assert body.index('id="section-control"') < body.index('id="overviewCards"')
    assert 'id="probe"' not in body
    assert 'class="tabs"' not in body
    assert "static/app.css" in body
    assert "static/app.js" in body


def test_dashboard_javascript_preserves_snapshot_only_guards():
    script = (
        Path(__file__).parents[1] / "app" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    assert "PTZ_SNAPSHOT_INTERVAL_MS=1000" in script
    assert "manualSnapshotBusy" in script
    assert "ptzSnapshotRequests" in script
    assert "allowManualBusy" in script


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


def test_unknown_api_route_is_not_exposed():
    client = main.app.test_client()
    assert client.get("/api/unsupported").status_code == 404


def test_frontend_marks_ptz_lens_and_captures_extra_final_frames():
    script = (
        Path(__file__).parents[1] / "app" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    assert "feed-tile-ptz" in script
    assert "feedPtzMarker" in script
    assert "const delays=[220,900,900]" in script
    assert "marker.hidden=!isPtz" in script


def test_frontend_uses_one_hertz_ptz_snapshot_refresh():
    client = main.app.test_client()
    script = client.get("/static/app.js").get_data(as_text=True)

    assert "PTZ_SNAPSHOT_INTERVAL_MS=1000" in script
    assert "ptzSnapshotLoop" in script
