import threading
import time

import main


def setup_function():
    main._snapshot_cache.clear()
    main._ptz_sequences.clear()
    main.update_state(
        online=False,
        authenticated=False,
        probe_running=False,
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
