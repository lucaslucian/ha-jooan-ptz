import threading
import time

import main


def setup_function():
    main._snapshot_cache.clear()


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
