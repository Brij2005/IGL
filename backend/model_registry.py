"""Centralized AI model capability and scene registry.

This module is the single source of truth describing *what each registered AI
model can detect*, which *input types* it supports, and *how a live scene maps
to the models that should run on it*.

It deliberately contains no weights and performs no training. It only describes
capabilities that are already registered in ``ai_models`` and ties them to the
scene-based automatic model selection used by the inference workers.

Design rules (enforced across the system):

    * A model that is not ``AVAILABLE`` is never selected automatically.
    * A model is only ever paired with input types it can genuinely handle
      (an RGB COCO model is never run on thermal footage).
    * Scene labels are *hints* derived from actual frame content by the
      scene analyzer, never from the camera name or its configured purpose.
"""

from __future__ import annotations

# Canonical scene labels the scene analyzer can emit.
SCENE_GENERAL = "GENERAL"
SCENE_WATER = "WATER"
SCENE_THERMAL = "THERMAL"
SCENE_FACE = "FACE"
SCENE_CORROSION = "CORROSION"

# Input source families. These mirror ``camera.source_type`` values while
# grouping thermal variants together so capability checks stay simple.
INPUT_RGB = "RGB"
INPUT_THERMAL = "THERMAL"

_THERMAL_SOURCE_TYPES = {"thermal", "thermal_rtsp"}


def input_family_for_source(source_type: str) -> str:
    """Map a camera ``source_type`` to an input family (RGB or THERMAL)."""
    normalized = (source_type or "").lower()
    if normalized in _THERMAL_SOURCE_TYPES:
        return INPUT_THERMAL
    return INPUT_RGB


# Per-model capability contract. Keyed by the registered ``ai_models.id``.
# ``scenes`` are the scene labels under which the model is a good candidate.
# ``inputs`` restricts the model to input families it genuinely supports.
MODEL_CAPABILITIES = {
    "model-yolov8n": {
        "display_name": "YOLOv8n General Safety",
        "purpose": "General safety objects (person, vehicle, cell phone, etc.)",
        "inputs": [INPUT_RGB],
        "scenes": [SCENE_GENERAL, SCENE_FACE],
    },
    "model-water-leakage": {
        "display_name": "Water Pipeline Leakage",
        "purpose": "Water pipeline leakage classes (dripping, spraying, flow, joint leakage, accumulation, dry).",
        "inputs": [INPUT_RGB],
        "scenes": [SCENE_WATER],
    },
    "model-thermal-person": {
        "display_name": "Thermal Person Detection",
        "purpose": "Person detection on calibrated thermal footage.",
        "inputs": [INPUT_THERMAL],
        "scenes": [SCENE_THERMAL],
    },
    "model-pipe-corrosion": {
        "display_name": "Pipeline Corrosion Detection",
        "purpose": "Surface corrosion / rust appearance on pipelines (review required).",
        "inputs": [INPUT_RGB],
        "scenes": [SCENE_CORROSION],
    },
}

# Fallback capability for models that are registered but not yet described.
_DEFAULT_CAPABILITY = {
    "display_name": None,
    "purpose": "Registered model without a declared scene capability.",
    "inputs": [INPUT_RGB],
    "scenes": [SCENE_GENERAL],
}


def capability_for(model_id: str) -> dict:
    """Return the capability contract for a model id (never raises)."""
    return MODEL_CAPABILITIES.get(model_id, dict(_DEFAULT_CAPABILITY, id=model_id))


def supports_input(model_id: str, input_family: str) -> bool:
    """True when the model can genuinely run on the given input family."""
    capability = capability_for(model_id)
    return input_family in capability.get("inputs", [INPUT_RGB])


def is_candidate_for_scene(model_id: str, scene: str, input_family: str) -> bool:
    """True when the model is a compatible candidate for a scene + input.

    A candidate must:
        * support the camera's input family, and
        * list the scene (or the general scene) among its scenes.
    """
    capability = capability_for(model_id)
    if input_family not in capability.get("inputs", [INPUT_RGB]):
        return False
    scenes = capability.get("scenes", [SCENE_GENERAL])
    if scene in scenes:
        return True
    # General-purpose models remain candidates in every scene.
    return SCENE_GENERAL in scenes


def describe_registry() -> list:
    """Return the full static capability registry for introspection/tests."""
    return [
        {
            "model_id": model_id,
            "display_name": capability.get("display_name"),
            "purpose": capability.get("purpose"),
            "inputs": capability.get("inputs", []),
            "scenes": capability.get("scenes", []),
        }
        for model_id, capability in MODEL_CAPABILITIES.items()
    ]