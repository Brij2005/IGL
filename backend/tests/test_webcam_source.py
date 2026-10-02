"""Unit tests for the Windows webcam backend fallback sequence."""

from pathlib import Path
import sys

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
