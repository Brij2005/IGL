import threading
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.continuous_health import ContinuousCameraHealthWorker


def test_continuous_health_worker_starts_and_stops_cleanly(monkeypatch):
    observed_poll = threading.Event()
    worker = ContinuousCameraHealthWorker(interval_seconds=60)
    monkeypatch.setattr(worker, "poll_once", observed_poll.set)
    worker.start()
    try:
        assert observed_poll.wait(timeout=2)
        assert worker.status == "RUNNING"
    finally:
        worker.stop()
    assert worker.status == "STOPPED"
    assert worker._thread is not None
    assert not worker._thread.is_alive()