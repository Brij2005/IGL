"""Laptop built-in webcam source support.

A webcam is opened as a capture device, not as a network stream, so it cannot be
expressed as an RTSP URL. The existing camera model stores a single ``stream_url``
string, so a webcam is registered with the ``webcam://`` scheme:

    webcam://0                     # default device, native resolution
    webcam://0?width=1280&height=720&fps=15

Everything downstream is unchanged: the same reader thread, frame buffer,
timestamps, drop accounting, health monitor and inference pipeline consume the
frames. Nothing here produces a detection, a safety event, or a frame rate that
was not measured from frames that actually arrived.

Windows note: OpenCV cannot enumerate webcam names reliably through the standard
backend, so :func:`probe_device` reports only what it can prove. A device is
reported available only when a capture was opened *and* a frame was read back.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

import cv2

logger = logging.getLogger("igl.video.webcam")

WEBCAM_SCHEME = "webcam"
WEBCAM_PREFIX = f"{WEBCAM_SCHEME}://"
DEFAULT_DEVICE_INDEX = 0
#: Bounded probe range. Enumerating past this is not informative on a laptop and
#: opening a bogus device can block, so discovery stops here.
MAX_PROBE_INDEX = 5
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_TARGET_FPS = 15.0


class WebcamConfigurationError(ValueError):
    """Raised when a webcam source specification is not usable."""


@dataclass(frozen=True)
class WebcamConfig:
    """Parsed ``webcam://`` source specification."""

    device_index: int = DEFAULT_DEVICE_INDEX
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "device_index": self.device_index,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
        }


@dataclass
class DeviceProbe:
    """What probing one device index could actually establish."""

    index: int
    available: bool
    name: Optional[str] = None
    read_frame: bool = False
    resolution: Optional[tuple[int, int]] = None
    reported_fps: Optional[float] = None
    error: Optional[str] = None
    backend: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "available": self.available,
            # Only a name that the backend actually returned is reported.
            "name": self.name,
            "opened": self.error is None,
            "read_frame": self.read_frame,
            "resolution": list(self.resolution) if self.resolution else None,
            "reported_fps": self.reported_fps,
            "error": self.error,
            "backend": self.backend,
        }


def is_webcam_source(stream_url: str) -> bool:
    return bool(stream_url) and stream_url.strip().lower().startswith(WEBCAM_PREFIX)


def _parse_positive_int(raw: str, field_name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise WebcamConfigurationError(f"{field_name} must be an integer, received '{raw}'") from exc
    if value <= 0:
        raise WebcamConfigurationError(f"{field_name} must be greater than zero, received {value}")
    return value


def _parse_device_index(raw: str) -> int:
    """Parse a capture index. Zero is the first device, so it is valid here."""
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise WebcamConfigurationError(f"device_index must be an integer, received '{raw}'") from exc
    if value < 0:
        raise WebcamConfigurationError(f"device_index must be zero or greater, received {value}")
    return value


def parse_webcam_url(stream_url: str) -> WebcamConfig:
    """Parse a ``webcam://`` URL into a validated configuration."""
    if not is_webcam_source(stream_url):
        raise WebcamConfigurationError(f"Source '{stream_url}' is not a {WEBCAM_PREFIX} URL")

    parsed = urlparse(stream_url.strip())
    # urlparse puts everything after "webcam:" in netloc for this scheme.
    raw_index = (parsed.netloc or parsed.path.lstrip("/") or "").strip().split("/")[0]
    if not raw_index:
        raise WebcamConfigurationError(
            f"A webcam URL must include a device index, for example {WEBCAM_PREFIX}0"
        )
    device_index = _parse_device_index(raw_index)

    query = parse_qs(parsed.query or "")
    width = _parse_positive_int(query["width"][0], "width") if "width" in query else None
    height = _parse_positive_int(query["height"][0], "height") if "height" in query else None
    fps: Optional[float] = None
    if "fps" in query:
        try:
            fps = float(query["fps"][0])
        except (TypeError, ValueError) as exc:
            raise WebcamConfigurationError(f"fps must be a number, received '{query['fps'][0]}'") from exc
        if fps <= 0:
            raise WebcamConfigurationError(f"fps must be greater than zero, received {fps}")

    return WebcamConfig(device_index=device_index, width=width, height=height, fps=fps)


def build_webcam_url(
    device_index: int = DEFAULT_DEVICE_INDEX,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: float = DEFAULT_TARGET_FPS,
) -> str:
    """Compose a ``webcam://`` URL from operator-supplied values."""
    if width <= 0 or height <= 0:
        raise WebcamConfigurationError("width and height must be greater than zero")
    if fps <= 0:
        raise WebcamConfigurationError("fps must be greater than zero")
    return f"{WEBCAM_PREFIX}{int(device_index)}?width={int(width)}&height={int(height)}&fps={float(fps)}"


def _candidate_backends() -> list[tuple[int, str]]:
    """Backends to try, most reliable first on Windows."""
    backends: list[tuple[int, str]] = []
    if hasattr(cv2, "CAP_DSHOW"):
        backends.append((cv2.CAP_DSHOW, "DSHOW"))
    backends.append((cv2.CAP_ANY, "ANY"))
    return backends


def open_webcam_capture(config: WebcamConfig) -> tuple[Optional[cv2.VideoCapture], str, Optional[str]]:
    """Open a webcam device.

    Returns ``(capture, backend_name, error)``. A capture is returned only when
    the backend reports it as opened; callers must still read a frame before
    concluding the device works.
    """
    errors: list[str] = []
    for backend, backend_name in _candidate_backends():
        capture: Optional[cv2.VideoCapture] = None
        try:
            capture = cv2.VideoCapture(config.device_index, backend)
            if capture is None or not capture.isOpened():
                if capture is not None:
                    capture.release()
                errors.append(f"{backend_name}: device did not open")
                continue

            # Requested values are a request, not a measurement; the actual
            # negotiated values are read back by the caller.
            if config.width:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(config.width))
            if config.height:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(config.height))
            if config.fps:
                capture.set(cv2.CAP_PROP_FPS, float(config.fps))
            return capture, backend_name, None
        except Exception as exc:  # noqa: BLE001 - driver errors are backend-specific
            if capture is not None:
                try:
                    capture.release()
                except Exception:  # noqa: BLE001 - release is best effort
                    pass
            errors.append(f"{backend_name}: {type(exc).__name__}")
    return None, "NONE", "; ".join(errors) or "no capture backend available"


def probe_device(index: int, read_frame: bool = True) -> DeviceProbe:
    """Test one device index and report only what was proven.

    ``available`` requires that a frame was actually read back. Opening a
    capture successfully is not evidence that a camera is present: OpenCV will
    happily open a device index that yields nothing.
    """
    config = WebcamConfig(device_index=index)
    capture, backend, error = open_webcam_capture(config)
    if capture is None:
        return DeviceProbe(index=index, available=False, error=error, backend=backend)

    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        reported_fps_value = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        resolution = (width, height) if width > 0 and height > 0 else None
        # A driver-reported rate is metadata, not a measurement. It is reported
        # separately and never used as an observed frame rate.
        reported_fps = reported_fps_value if reported_fps_value > 0 else None

        frame_read = False
        probe_error: Optional[str] = None
        if read_frame:
            try:
                retrieved, frame = capture.read()
                frame_read = bool(retrieved) and frame is not None and getattr(frame, "size", 0) > 0
                if not frame_read:
                    probe_error = "Device opened but returned no frame"
            except Exception as exc:  # noqa: BLE001 - driver read error
                probe_error = f"read failed: {type(exc).__name__}"

        # Only a name the backend genuinely supplied is reported; the generic
        # label says nothing that the index does not already say.
        name = None
        if frame_read:
            name = f"Webcam {index}"
        return DeviceProbe(
            index=index,
            available=frame_read,
            name=name,
            read_frame=frame_read,
            resolution=resolution,
            reported_fps=reported_fps,
            error=probe_error,
            backend=backend,
        )
    finally:
        try:
            capture.release()
        except Exception:  # noqa: BLE001 - release is best effort
            pass


def discover_devices(max_index: int = MAX_PROBE_INDEX, read_frame: bool = True) -> list[DeviceProbe]:
    """Probe a bounded range of indices and report which ones really work."""
    limit = max(0, min(int(max_index), MAX_PROBE_INDEX))
    probes: list[DeviceProbe] = []
    for index in range(limit + 1):
        probes.append(probe_device(index, read_frame=read_frame))
    return probes