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


def test_operator_stopped_camera_is_not_restarted_or_reported_as_failure(monkeypatch):
    from app.services.continuous_health import pipeline_manager, health_monitor

    camera = type("CameraStub", (), {"id": "operator-stopped-camera", "stream_url": "webcam://0", "fps": 15})()
    observed_dispatch_flags = []

    class CameraQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [camera]

    class SessionStub:
        def query(self, *_args):
            return CameraQuery()

    monkeypatch.setattr(pipeline_manager, "is_operator_stopped", lambda _camera_id: True)
    monkeypatch.setattr(
        pipeline_manager,
        "start_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stopped camera restarted")),
    )
    monkeypatch.setattr(
        health_monitor,
        "evaluate_camera_health",
        lambda _db, _camera, dispatch_events: observed_dispatch_flags.append(dispatch_events),
    )

    ContinuousCameraHealthWorker().poll_once(db=SessionStub())

    assert observed_dispatch_flags == [False]


def test_unstarted_webcam_is_not_auto_started_by_health_worker(monkeypatch):
    from app.services.continuous_health import pipeline_manager, health_monitor, ingestion_manager

    camera = type("CameraStub", (), {"id": "unstarted-webcam", "stream_url": "webcam://0", "fps": 15})()
    observed_dispatch_flags = []

    class CameraQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [camera]

    class SessionStub:
        def query(self, *_args):
            return CameraQuery()

    monkeypatch.setattr(pipeline_manager, "is_operator_stopped", lambda _camera_id: False)
    monkeypatch.setattr(ingestion_manager, "get_reader", lambda _camera_id: None)
    monkeypatch.setattr(
        pipeline_manager,
        "start_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("webcam auto-started")),
    )
    monkeypatch.setattr(
        health_monitor,
        "evaluate_camera_health",
        lambda _db, _camera, dispatch_events: observed_dispatch_flags.append(dispatch_events),
    )

    ContinuousCameraHealthWorker().poll_once(db=SessionStub())

    assert observed_dispatch_flags == [False]