"""Unit tests for the Windows webcam backend fallback sequence."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import webcam_source


def test_windows_capture_tries_dshow_then_msmf_then_automatic(monkeypatch):
    attempts = []

    class FakeCapture:
        def __init__(self, opened):
            self.opened = opened
            self.released = False

        def isOpened(self):
            return self.opened

        def release(self):
            self.released = True

    captures = []

    def fake_video_capture(index, backend):
        attempts.append((index, backend))
        capture = FakeCapture(opened=(backend == webcam_source.cv2.CAP_MSMF))
        captures.append(capture)
        return capture

    monkeypatch.setattr(webcam_source.cv2, "VideoCapture", fake_video_capture)
    capture, backend_name, error = webcam_source.open_webcam_capture(
        webcam_source.WebcamConfig(device_index=2)
    )

    assert attempts == [
        (2, webcam_source.cv2.CAP_DSHOW),
        (2, webcam_source.cv2.CAP_MSMF),
    ]
    assert captures[0].released is True
    assert capture is captures[1]
    assert backend_name == "MSMF"
    assert error is None


def _recording_capture(opened_backends, attempts):
    class FakeCapture:
        def __init__(self, opened):
            self.opened = opened
            self.released = False

        def isOpened(self):
            return self.opened

        def set(self, *_args):
            return True

        def release(self):
            self.released = True

    def fake_video_capture(index, backend):
        attempts.append((index, backend))
        return FakeCapture(opened=backend in opened_backends)

    return fake_video_capture


def test_preferred_backend_is_tried_first_and_the_probe_order_remains_the_fallback(monkeypatch):
    """A camera may ask for a backend; the automatic probe still backs it up."""
    attempts = []
    monkeypatch.setattr(
        webcam_source.cv2,
        "VideoCapture",
        _recording_capture({webcam_source.cv2.CAP_ANY}, attempts),
    )

    capture, backend_name, error = webcam_source.open_webcam_capture(
        webcam_source.WebcamConfig(device_index=1, backend="MSMF")
    )

    assert [name for _index, name in webcam_source._candidate_backends("MSMF")] == [
        "MSMF",
        "DSHOW",
        "ANY",
    ]
    # MSMF was asked for first and could not open, so the default order still
    # got its turn and ANY succeeded.
    assert attempts == [
        (1, webcam_source.cv2.CAP_MSMF),
        (1, webcam_source.cv2.CAP_DSHOW),
        (1, webcam_source.cv2.CAP_ANY),
    ]
    assert capture is not None
    assert backend_name == "ANY"
    assert error is None


def test_reported_backend_is_the_one_that_opened_not_the_one_requested(monkeypatch):
    """Telemetry must name the backend that really opened the capture."""
    attempts = []
    monkeypatch.setattr(
        webcam_source.cv2,
        "VideoCapture",
        _recording_capture({webcam_source.cv2.CAP_DSHOW}, attempts),
    )

    _capture, backend_name, _error = webcam_source.open_webcam_capture(
        webcam_source.WebcamConfig(device_index=0, backend="DSHOW")
    )

    assert backend_name == "DSHOW"


def test_no_preference_keeps_the_existing_probe_order(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        webcam_source.cv2,
        "VideoCapture",
        _recording_capture(set(), attempts),
    )

    _capture, backend_name, error = webcam_source.open_webcam_capture(
        webcam_source.WebcamConfig(device_index=0)
    )

    assert [name for _index, name in webcam_source._candidate_backends(None)] == [
        "DSHOW",
        "MSMF",
        "ANY",
    ]
    assert attempts[-1] == (0, webcam_source.cv2.CAP_ANY)
    assert backend_name == "NONE"
    assert error


def test_webcam_url_carries_the_requested_backend():
    url = webcam_source.build_webcam_url(device_index=0, width=640, height=480, fps=10.0, backend="dshow")
    config = webcam_source.parse_webcam_url(url)

    assert config.backend == "DSHOW"
    assert config.device_index == 0
    assert webcam_source.build_webcam_url().count("backend=") == 0
    assert webcam_source.parse_webcam_url(webcam_source.build_webcam_url()).backend is None


def test_an_unknown_backend_is_a_configuration_error_not_a_silent_default():
    with pytest.raises(webcam_source.WebcamConfigurationError):
        webcam_source.normalize_backend("V4L2")
    with pytest.raises(webcam_source.WebcamConfigurationError):
        webcam_source.parse_webcam_url("webcam://0?backend=V4L2")
