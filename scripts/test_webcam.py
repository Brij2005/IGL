"""Laptop webcam acquisition check.

This script proves exactly one thing: whether this machine can open a real
webcam and deliver real frames. It says nothing about detection accuracy,
safety detection, IGL validation, or production readiness.

Usage from the project root:

    python scripts/test_webcam.py
    python scripts/test_webcam.py --index 0 --seconds 10
    python scripts/test_webcam.py --index 0 --seconds 10 --width 1280 --height 720 --fps 15

The report states what was measured. When no frame arrives, the report says so
and exits non-zero rather than reporting a rate it did not observe.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import cv2  # noqa: E402

try:
    from app.services.webcam_source import (
        MAX_PROBE_INDEX,
        WebcamConfig,
        build_webcam_url,
        discover_devices,
        open_webcam_capture,
        parse_webcam_url,
    )
except ImportError:  # pragma: no cover
    from backend.app.services.webcam_source import (
        MAX_PROBE_INDEX,
        WebcamConfig,
        build_webcam_url,
        discover_devices,
        open_webcam_capture,
        parse_webcam_url,
    )

# Model state is read from configuration only. No weights are ever downloaded,
# and no substitute model is loaded.
try:
    from app.config import settings
except ImportError:  # pragma: no cover
    from backend.app.config import settings


def model_configuration_state() -> str:
    """Whether usable weights are configured. Never triggers a download."""
    path = settings.MODEL_WEIGHTS_PATH
    if not path:
        return "MODEL_NOT_CONFIGURED"
    weights = Path(path)
    if not weights.is_absolute():
        weights = PROJECT_ROOT / weights
    if not weights.exists():
        return "MODEL_WEIGHTS_NOT_FOUND"
    return "MODEL_CONFIGURED"


def run_capture(config: WebcamConfig, seconds: float) -> dict:
    """Open the device and count real frames for a fixed duration."""
    capture, backend, open_error = open_webcam_capture(config)
    if capture is None:
        return {
            "device_index": config.device_index,
            "opened": False,
            "capture_backend": backend,
            "error": open_error,
            "frames_received": 0,
            "real_webcam_source": False,
        }

    frames = 0
    read_failures = 0
    invalid_frames = 0
    observed_resolution = None
    reported_fps = None
    first_frame_monotonic = None
    last_frame_monotonic = None

    try:
        started = time.monotonic()
        deadline = started + seconds
        while time.monotonic() < deadline:
            retrieved, frame = capture.read()
            if not retrieved or frame is None:
                read_failures += 1
                continue
            if getattr(frame, "size", 0) == 0 or getattr(frame, "ndim", 0) < 2:
                invalid_frames += 1
                continue
            now = time.monotonic()
            if first_frame_monotonic is None:
                first_frame_monotonic = now
            last_frame_monotonic = now
            frames += 1
            observed_resolution = (int(frame.shape[1]), int(frame.shape[0]))

        elapsed = time.monotonic() - started
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        driver_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        reported_fps = driver_fps if driver_fps > 0 else None
    finally:
        capture.release()

    # Frame rate is only computed from frames that actually arrived. With zero
    # frames the rate stays None instead of becoming 0.0, which would read as a
    # measured result.
    measured_fps = round(frames / elapsed, 2) if frames > 0 and elapsed > 0 else None

    return {
        "device_index": config.device_index,
        "opened": True,
        "capture_backend": backend,
        "error": None,
        "frames_received": frames,
        "elapsed_seconds": round(elapsed, 3),
        "measured_fps": measured_fps,
        "observed_resolution": list(observed_resolution) if observed_resolution else None,
        "negotiated_resolution": [width, height] if width > 0 and height > 0 else None,
        "source_reported_fps": reported_fps,
        "read_failures": read_failures,
        "invalid_frames": invalid_frames,
        # A camera is considered a real source only when frames arrived.
        "real_webcam_source": frames > 0,
    }


def build_report(result: dict, discovery: list[dict], model_state: str) -> dict:
    real_source = bool(result.get("real_webcam_source"))
    return {
        "webcam_check": "REAL_WEBCAM_SOURCE_YES" if real_source else "REAL_WEBCAM_SOURCE_NO",
        "real_webcam_source": "YES" if real_source else "NO",
        "device_index": result.get("device_index"),
        "capture_backend": result.get("capture_backend"),
        "frames_received": result.get("frames_received", 0),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "measured_fps": result.get("measured_fps"),
        "observed_resolution": result.get("observed_resolution"),
        "negotiated_resolution": result.get("negotiated_resolution"),
        "source_reported_fps": result.get("source_reported_fps"),
        "read_failures": result.get("read_failures", 0),
        "invalid_frames": result.get("invalid_frames", 0),
        "error": result.get("error"),
        "discovered_devices": discovery,
        "MODEL STATUS": model_state,
        "INFERENCE VALIDATION": "NOT PERFORMED",
        "IGL VALIDATION": "NOT VALIDATED",
        "SAFETY DETECTIONS": "NONE - no detector ran",
        "NOTE": (
            "A working webcam proves only that the real video acquisition path "
            "works. It does not validate PPE, helmet, fire, smoke, fall, "
            "proximity or unsafe-behaviour detection, and it is not IGL validation."
        ),
    }


def print_report(report: dict) -> None:
    print()
    print("=" * 68)
    print("LAPTOP WEBCAM ACQUISITION REPORT")
    print("=" * 68)
    for key, value in report.items():
        if key == "discovered_devices":
            continue
        print(f"{key:28}: {value}")
    devices = report.get("discovered_devices") or []
    print(f"{'discovered_devices':28}:")
    for device in devices:
        print(
            f"  index {device['index']}: available={device['available']} "
            f"read_frame={device['read_frame']} backend={device['backend']} "
            f"resolution={device['resolution']} error={device['error']}"
        )
    print("=" * 68)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a real laptop webcam is available.")
    parser.add_argument("--index", type=int, default=0, help="Webcam device index to test (default 0)")
    parser.add_argument("--seconds", type=float, default=5.0, help="Capture duration in seconds (default 5)")
    parser.add_argument("--width", type=int, default=1280, help="Requested capture width")
    parser.add_argument("--height", type=int, default=720, help="Requested capture height")
    parser.add_argument("--fps", type=float, default=15.0, help="Requested capture rate")
    parser.add_argument("--discover-only", action="store_true", help="Only probe indices, do not capture")
    parser.add_argument("--max-probe-index", type=int, default=MAX_PROBE_INDEX, help="Highest index to probe")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON only")
    arguments = parser.parse_args()

    discovery = [probe.as_dict() for probe in discover_devices(max_index=arguments.max_probe_index)]

    if arguments.discover_only:
        result = {"device_index": None, "real_webcam_source": False, "error": "Discovery only"}
    else:
        # The capture configuration is expressed through the same webcam URL the
        # camera API uses, so the script cannot drift from the served behaviour.
        url = build_webcam_url(arguments.index, arguments.width, arguments.height, arguments.fps)
        config = parse_webcam_url(url)
        result = run_capture(config, seconds=arguments.seconds)

    report = build_report(result, discovery, model_configuration_state())
    if arguments.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    # Exit code reflects only whether real frames were captured.
    return 0 if report["real_webcam_source"] == "YES" else 1


if __name__ == "__main__":
    raise SystemExit(main())