"""Radiometric thermal ingestion, calibration and integrity validation.

This module is deliberately strict about temperature data:

    * Only genuine radiometric sources (per-pixel temperature arrays, or a
      calibrated thermal frame accompanied by sensor + calibration metadata)
      are accepted as temperature-capable.
    * Ordinary RGB images are rejected as thermal sources.
    * Temperatures are never invented, interpolated or defaulted. If a value is
      missing or uncalibrated the reading is refused and the reason reported.
    * Device/sensor identity and calibration provenance are preserved with every
      reading so downstream consumers can audit it.

Supported radiometric encodings:
    * ``.npy``  - 2-D float array of degrees Celsius
    * ``.csv``  - 2-D numeric grid of degrees Celsius
    * ``.json`` - {"width":..,"height":..,"celsius":[[..]], ...}

CLI:
    python thermal_ingestion.py inspect --file <path>
    python thermal_ingestion.py record  --file <path> --camera-id <id> [--threshold-c 37.5]
"""

import argparse
import csv
import io
import json
import sys
import uuid
from pathlib import Path

from database import get_db_connection, now_iso

# Plausible industrial/human-surface temperature window (°C). Values outside
# this range indicate a malformed or mis-calibrated export, not a real reading.
PLAUSIBLE_MIN_C = -40.0
PLAUSIBLE_MAX_C = 200.0


class ThermalDataError(ValueError):
    """Raised when thermal data is missing, malformed, uncalibrated or not thermal."""


SUPPORTED_RADIOMETRIC_SUFFIXES = {".npy", ".csv", ".json"}
RGB_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _reject_rgb(path: Path) -> None:
    if path.suffix.lower() in RGB_SUFFIXES:
        raise ThermalDataError(
            "An ordinary RGB image is not a thermal source. Provide a radiometric "
            "array (.npy/.csv/.json) with per-pixel Celsius values."
        )


def load_radiometric_array(path: str):
    """Load a genuine per-pixel Celsius grid. Never fabricates values."""
    file_path = Path(path)
    if not file_path.exists():
        raise ThermalDataError(f"Thermal file not found: {file_path}")
    _reject_rgb(file_path)
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_RADIOMETRIC_SUFFIXES:
        raise ThermalDataError(
            f"Unsupported radiometric format '{suffix}'. Expected one of {sorted(SUPPORTED_RADIOMETRIC_SUFFIXES)}"
        )

    meta = {}
    if suffix == ".npy":
        import numpy as np

        array = np.load(file_path)
        if array.ndim != 2:
            raise ThermalDataError("Radiometric .npy must be a 2-D array of Celsius values")
        grid = array.astype(float).tolist()
    elif suffix == ".csv":
        rows = list(csv.reader(io.StringIO(file_path.read_text(encoding="utf-8"))))
        if not rows:
            raise ThermalDataError("Radiometric .csv is empty")
        try:
            grid = [[float(cell) for cell in row] for row in rows if row]
        except ValueError as exc:
            raise ThermalDataError(f"Radiometric .csv contains non-numeric values: {exc}") from exc
    else:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            grid = payload.get("celsius")
            meta = {k: v for k, v in payload.items() if k != "celsius"}
        else:
            grid = payload
        if not isinstance(grid, list) or not grid:
            raise ThermalDataError("Radiometric JSON must contain a non-empty 'celsius' grid")
        try:
            grid = [[float(cell) for cell in row] for row in grid]
        except (TypeError, ValueError) as exc:
            raise ThermalDataError(f"Radiometric JSON grid contains non-numeric values: {exc}") from exc

    if not grid or not grid[0]:
        raise ThermalDataError("Radiometric grid is empty")
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        raise ThermalDataError("Radiometric grid is ragged; rows must be equal length")

    flat = [value for row in grid for value in row]
    return {
        "width": width,
        "height": len(grid),
        "grid": grid,
        "min_c": min(flat),
        "max_c": max(flat),
        "mean_c": sum(flat) / len(flat),
        "meta": meta,
        "source_format": suffix.lstrip("."),
    }


def validate_calibration(meta: dict) -> dict:
    """Verify that a genuine calibration record is present and complete.

    Temperature is only reported when calibration provenance exists. Missing or
    incomplete calibration means uncalibrated, and the caller must refuse.
    """
    if not isinstance(meta, dict) or not meta:
        return {"calibrated": False, "reason": "no calibration metadata supplied"}
    required = ["sensor_model", "calibration_date", "emissivity"]
    missing = [key for key in required if meta.get(key) in (None, "")]
    if missing:
        return {"calibrated": False, "reason": f"missing calibration fields: {missing}"}
    try:
        emissivity = float(meta["emissivity"])
    except (TypeError, ValueError):
        return {"calibrated": False, "reason": "emissivity is not numeric"}
    if not 0.0 < emissivity <= 1.0:
        return {"calibrated": False, "reason": "emissivity must be in (0, 1]"}
    return {
        "calibrated": True,
        "sensor_model": meta["sensor_model"],
        "calibration_date": meta["calibration_date"],
        "emissivity": emissivity,
        "temperature_range_c": [meta.get("min_range_c"), meta.get("max_range_c")],
        "resolution": [meta.get("width"), meta.get("height")],
    }


def validate_readings(array_info: dict) -> dict:
    """Sanity-check that values look like real temperatures, not fabricated ones."""
    out_of_range = 0
    for value in (v for row in array_info["grid"] for v in row):
        if value < PLAUSIBLE_MIN_C or value > PLAUSIBLE_MAX_C:
            out_of_range += 1
    total = array_info["width"] * array_info["height"]
    return {
        "plausible": out_of_range == 0,
        "out_of_range_pixels": out_of_range,
        "total_pixels": total,
        "range_c": [array_info["min_c"], array_info["max_c"]],
        "mean_c": round(array_info["mean_c"], 3),
    }


def inspect_thermal_file(path: str) -> dict:
    """Inspect a thermal file and report exactly what it is and whether it is usable."""
    array_info = load_radiometric_array(path)
    calibration = validate_calibration(array_info["meta"])
    readings = validate_readings(array_info)
    return {
        "file": str(path),
        "is_thermal": True,
        "source_format": array_info["source_format"],
        "resolution": [array_info["width"], array_info["height"]],
        "temperature_range_c": [array_info["min_c"], array_info["max_c"]],
        "calibration": calibration,
        "readings": readings,
        "temperature_usable": bool(calibration["calibrated"] and readings["plausible"]),
        "sensor": array_info["meta"].get("sensor_model"),
        "note": "Temperatures are only reported when a complete calibration record is present.",
    }


def record_thermal_reading(
    path: str,
    camera_id: str,
    threshold_c: float = None,
    track_id: str = None,
    person_id: str = None,
    bbox=None,
) -> dict:
    """Persist a calibrated thermal reading, or refuse honestly."""
    array_info = load_radiometric_array(path)
    calibration = validate_calibration(array_info["meta"])
    readings = validate_readings(array_info)

    if not calibration["calibrated"]:
        raise ThermalDataError(
            "Refusing to store temperature: " + calibration["reason"]
        )
    if not readings["plausible"]:
        raise ThermalDataError(
            "Refusing to store temperature: values outside plausible range "
            f"{PLAUSIBLE_MIN_C}..{PLAUSIBLE_MAX_C} °C"
        )

    mean_c = round(array_info["mean_c"], 2)
    threshold_exceeded = bool(threshold_c is not None and mean_c >= float(threshold_c))

    evidence_id = None
    try:
        import hashlib

        import numpy as np

        evidence_id = f"THM-{uuid.uuid4().hex[:10].upper()}"
        from config import EVIDENCE_DIR

        grid = np.array(array_info["grid"], dtype=float)
        normalised = ((grid - grid.min()) / (grid.max() - grid.min() + 1e-9) * 255).astype("uint8")
        import cv2

        ok, buffer = cv2.imencode(".png", normalised)
        if ok:
            target = Path(EVIDENCE_DIR) / f"{evidence_id}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(buffer.tobytes())
            conn = get_db_connection()
            conn.execute(
                "INSERT INTO evidence_files (id, camera_id, file_path, content_type, sha256) "
                "VALUES (?, ?, ?, 'image/png', ?)",
                (evidence_id, camera_id, str(target), hashlib.sha256(buffer.tobytes()).hexdigest()),
            )
            conn.commit()
            conn.close()
    except Exception:
        evidence_id = None

    reading_id = f"THM-{uuid.uuid4().hex[:10].upper()}"
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO thermal_readings
        (id, camera_id, track_id, person_id, temperature_c, threshold_c, threshold_exceeded,
         bbox_json, evidence_id, calibrated, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            reading_id,
            camera_id,
            track_id,
            person_id,
            mean_c,
            threshold_c,
            1 if threshold_exceeded else 0,
            json.dumps(bbox) if bbox else None,
            evidence_id,
            json.dumps(
                {
                    "sensor_model": calibration["sensor_model"],
                    "calibration_date": calibration["calibration_date"],
                    "emissivity": calibration["emissivity"],
                    "resolution": [array_info["width"], array_info["height"]],
                    "observed_range_c": [array_info["min_c"], array_info["max_c"]],
                    "source_format": array_info["source_format"],
                    "note": "Calibrated radiometric reading. Not a medical diagnosis.",
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "id": reading_id,
        "camera_id": camera_id,
        "temperature_c": mean_c,
        "threshold_c": threshold_c,
        "threshold_exceeded": threshold_exceeded,
        "calibrated": True,
        "sensor_model": calibration["sensor_model"],
        "evidence_id": evidence_id,
        "recorded_at": now_iso(),
    }


def main():
    parser = argparse.ArgumentParser(description="IGL radiometric thermal ingestion utility")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("--file", required=True)

    p_record = sub.add_parser("record")
    p_record.add_argument("--file", required=True)
    p_record.add_argument("--camera-id", required=True)
    p_record.add_argument("--threshold-c", type=float, default=None)
    p_record.add_argument("--track-id", default=None)
    p_record.add_argument("--person-id", default=None)

    args = parser.parse_args()
    if args.command == "inspect":
        payload = inspect_thermal_file(args.file)
    else:
        payload = record_thermal_reading(
            args.file,
            args.camera_id,
            threshold_c=args.threshold_c,
            track_id=args.track_id,
            person_id=args.person_id,
        )
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ThermalDataError as exc:
        print(json.dumps({"status": "REJECTED", "reason": str(exc)}, indent=2))
        sys.exit(1)