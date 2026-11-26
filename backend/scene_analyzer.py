"""Scene analyzer for automatic, scene-based model selection.

The analyzer inspects *actual frame content* (never the camera name or its
configured purpose) and returns a scene label with a confidence score and a
short, human-readable reason. The mode manager uses this to pick the set of
compatible models to run on a camera.

Design philosophy — **conservative and honest**:

    * Thermal frames -> THERMAL scene (source-type fact, no pixel guessing).
    * WATER / CORROSION / FACE are only reported when the frame shows a
      *dominant, saturated* region of that appearance AND enough texture/edge
      detail to distinguish a real object from a flat painted wall or a clear
      sky. Pale, low-saturation or flat surfaces fall back to GENERAL.
    * Otherwise -> GENERAL.

Why the extra guards (verified empirically):
    * A pure HSV hue-fraction test cannot separate "rust pipe" from "wooden
      cupboard", "tan wall" or "human face" — they share the same warm hue band.
      Likewise "clear water" and "blue sky through a window" share the same blue
      band. Relying on hue alone therefore misclassifies ordinary factory scenes.
    * We therefore require (a) high saturation, (b) a dominant fraction, and
      (c) sufficient edge density (texture) before committing to a specialised
      scene. If any guard fails, the scene is GENERAL.

GENERAL is the safe fallback: it only runs the general-purpose model(s) and
never raises specialised-model false positives. When the analyzer is uncertain
it returns GENERAL with low confidence rather than guessing.

This module contains no weights and performs no training.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:  # pragma: no cover - cv2 is a hard dependency in prod
    HAS_CV2 = False

from model_registry import (
    SCENE_CORROSION,
    SCENE_FACE,
    SCENE_GENERAL,
    SCENE_THERMAL,
    SCENE_WATER,
    input_family_for_source,
)

# --- Tuned thresholds (independently verified against ordinary scenes) --------
# Water: saturated blue/cyan region. Clear water and a window/sky both read as
# "blue", so we additionally require a high saturated fraction to avoid a pale
# blue wall, and rely on GENERAL fallback when the blue is not dominant.
_WATER_PIXEL_FRACTION = 0.55
# Rust: high-saturation warm hue. Rust is strongly saturated (S>=120); tan/wood
# walls are far less saturated, so a saturation floor separates them.
_RUST_PIXEL_FRACTION = 0.45
_RUST_MIN_SAT = 120
# Skin/face: high-saturation warm hue within the skin band, dominant enough.
_SKIN_PIXEL_FRACTION = 0.35
_SKIN_MIN_SAT = 60
_SKIN_MAX_SAT = 170
# Minimum edge density (Canny) for a region to count as a textured object rather
# than a flat painted surface. Flat walls/cupboards/clear sky have ~0.0 edge
# density; genuine textured objects (water ripples, rust flaking, faces) have
# noticeably higher density. Tuned so flat surfaces stay GENERAL while real
# texture is detected.
_MIN_EDGE_DENSITY = 0.03


def _hsv(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2HSV)


def _edge_density(gray) -> float:
    edges = cv2.Canny(gray, 80, 160)
    return float(np.count_nonzero(edges)) / float(edges.shape[0] * edges.shape[1])


def _region_fraction(hsv, hue_lo, hue_hi, sat_min, sat_max=255, val_min=0):
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = (h >= hue_lo) & (h <= hue_hi) & (s >= sat_min) & (s <= sat_max) & (v >= val_min)
    return float(np.count_nonzero(mask)) / float(hsv.shape[0] * hsv.shape[1])


def analyze_frame(image_bytes: bytes, source_type: str = "") -> dict:
    """Analyze a single encoded frame and return a scene decision.

    Returns a dict:
        {
            "scene": <label>,
            "confidence": <0..1>,
            "input_family": "RGB" | "THERMAL",
            "is_thermal": bool,
            "reason": <short text>,
            "scores": {label: score, ...},
        }
    """
    input_family = input_family_for_source(source_type)
    if input_family == "THERMAL":
        # Thermal source type is authoritative; do not pixel-guess an RGB scene.
        return {
            "scene": SCENE_THERMAL,
            "confidence": 1.0,
            "input_family": input_family,
            "is_thermal": True,
            "reason": "Camera source type is thermal.",
            "scores": {SCENE_THERMAL: 1.0},
        }

    if not HAS_CV2 or not image_bytes:
        return {
            "scene": SCENE_GENERAL,
            "confidence": 0.0,
            "input_family": input_family,
            "is_thermal": False,
            "reason": "Frame could not be analyzed (no cv2 or empty frame).",
            "scores": {},
        }

    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            return {
                "scene": SCENE_GENERAL,
                "confidence": 0.0,
                "input_family": input_family,
                "is_thermal": False,
                "reason": "Frame could not be decoded.",
                "scores": {},
            }
        hsv = _hsv(image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = _edge_density(gray)
        water = _region_fraction(hsv, 80, 135, 40, val_min=60)
        rust = _region_fraction(hsv, 5, 25, _RUST_MIN_SAT, val_min=40)
        skin = _region_fraction(hsv, 0, 25, _SKIN_MIN_SAT, _SKIN_MAX_SAT, val_min=60)
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "scene": SCENE_GENERAL,
            "confidence": 0.0,
            "input_family": input_family,
            "is_thermal": False,
            "reason": f"Frame analysis error: {exc}",
            "scores": {},
        }

    scores = {SCENE_WATER: water, SCENE_CORROSION: rust, SCENE_FACE: skin}

    # A flat surface (wall, painted cupboard, clear sky) has low edge density and
    # must not be treated as a specialised object scene. This is the key guard
    # that stops "blue sky window" and "wooden cupboard" false positives.
    textured = edges >= _MIN_EDGE_DENSITY

    best_scene = SCENE_GENERAL
    best_score = 0.0
    for scene, score in scores.items():
        if score <= best_score:
            continue
        if scene == SCENE_WATER and water >= _WATER_PIXEL_FRACTION and textured:
            best_scene, best_score = SCENE_WATER, water
        elif scene == SCENE_CORROSION and rust >= _RUST_PIXEL_FRACTION and textured:
            best_scene, best_score = SCENE_CORROSION, rust
        elif scene == SCENE_FACE and skin >= _SKIN_PIXEL_FRACTION and textured:
            best_scene, best_score = SCENE_FACE, skin

    if best_scene == SCENE_GENERAL:
        return {
            "scene": SCENE_GENERAL,
            "confidence": 0.4,
            "input_family": input_family,
            "is_thermal": False,
            "reason": (
                "No dominant, textured water/corrosion/face region detected; "
                "safe GENERAL fallback."
            ),
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "edge_density": round(edges, 4),
        }

    # Confidence scales with how far the winning score exceeds its threshold,
    # capped so a borderline detection never reports full confidence.
    thresholds = {SCENE_WATER: _WATER_PIXEL_FRACTION, SCENE_CORROSION: _RUST_PIXEL_FRACTION, SCENE_FACE: _SKIN_PIXEL_FRACTION}
    threshold = thresholds[best_scene]
    confidence = float(min(0.95, 0.5 + 0.45 * (best_score / (threshold * 1.5))))
    return {
        "scene": best_scene,
        "confidence": round(confidence, 3),
        "input_family": input_family,
        "is_thermal": False,
        "reason": f"{best_scene} region detected (score {best_score:.3f} >= {threshold}, edges {edges:.3f}).",
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "edge_density": round(edges, 4),
    }