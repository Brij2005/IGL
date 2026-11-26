"""Central mode manager: Automatic (default) / Manual model selection.

This is the single decision point that answers, for every running camera:
"which AI models should run on this camera right now?"

Two modes exist, persisted per camera in ``cameras.config_json`` under
``mode``:

    * ``AUTOMATIC`` (default) — the scene analyzer inspects live frames and
      the manager selects every *available* model that is a compatible
      candidate for the detected scene and the camera's input family.
    * ``MANUAL`` — the operator pins an explicit list of model ids. The
      manager still refuses to run models that are unavailable, untrained or
      incompatible with the camera's input type.

Guarantees:

    * A model that is not AVAILABLE (untrained / missing weights) is never
      selected in either mode.
    * An RGB model is never selected for a thermal camera, and vice-versa.
    * Automatic switching is *hysteresis-stabilised*: a new scene must be
      observed ``STABLE_SCENES_REQUIRED`` times before the active model set
      changes, so momentary frame noise cannot thrash model selection.

The manager keeps a small in-memory cache of the last decision per camera so
the UI (Camera Command Centre / Mapping views) can show "why these models are
running" without re-running inference.
"""

from __future__ import annotations

import json
import threading
import time

from database import get_db_connection
from model_registry import (
    SCENE_GENERAL,
    capability_for,
    input_family_for_source,
    is_candidate_for_scene,
    supports_input,
)

MODE_AUTOMATIC = "AUTOMATIC"
MODE_MANUAL = "MANUAL"
DEFAULT_MODE = MODE_AUTOMATIC

# Number of consecutive frames a scene must win before switching (hysteresis).
STABLE_SCENES_REQUIRED = 3
# Minimum seconds between automatic re-evaluation for a single camera.
RE_EVAL_INTERVAL_SECONDS = 2.0


def _load_config(camera: dict) -> dict:
    try:
        return json.loads(camera.get("config_json") or "{}")
    except Exception:
        return {}


def _available_model_ids() -> set:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id FROM ai_models WHERE status = 'AVAILABLE' AND weights_path IS NOT NULL AND weights_path != ''"
    ).fetchall()
    conn.close()
    return {row["id"] for row in rows}


class ModeManager:
    """Owns Automatic/Manual mode state and per-camera model selection."""

    def __init__(self):
        self._lock = threading.RLock()
        # camera_id -> {scene, confidence, reason, model_ids, mode, updated_at}
        self._last_decision = {}
        # camera_id -> {"scene": label, "count": n}
        self._scene_stability = {}
        # camera_id -> monotonic timestamp of last evaluation
        self._last_eval = {}

    # ------------------------------------------------------------------ mode
    def get_mode(self, camera: dict) -> str:
        config = _load_config(camera)
        mode = str(config.get("mode") or DEFAULT_MODE).upper()
        return MODE_MANUAL if mode == MODE_MANUAL else MODE_AUTOMATIC

    def set_mode(self, camera_id: str, mode: str) -> dict:
        mode = mode.upper()
        if mode not in {MODE_AUTOMATIC, MODE_MANUAL}:
            raise ValueError("mode must be AUTOMATIC or MANUAL")
        conn = get_db_connection()
        row = conn.execute("SELECT config_json FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        if not row:
            conn.close()
            raise ValueError("Camera not found")
        try:
            config = json.loads(row["config_json"] or "{}")
        except Exception:
            config = {}
        config["mode"] = mode
        conn.execute(
            "UPDATE cameras SET config_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(config), _now_iso(), camera_id),
        )
        conn.commit()
        conn.close()
        # Changing mode invalidates the cached decision so the next tick reselects.
        with self._lock:
            self._last_decision.pop(camera_id, None)
            self._scene_stability.pop(camera_id, None)
        return {"camera_id": camera_id, "mode": mode}

    # -------------------------------------------------------------- selection
    def select_models(self, camera: dict, scene: str, scene_confidence: float = 1.0) -> dict:
        """Return the model set to run for a camera under its current mode.

        Always filters by availability + input compatibility so that no
        untrained/unavailable/incompatible model can ever be selected.
        """
        camera_id = camera["id"]
        mode = self.get_mode(camera)
        input_family = input_family_for_source(camera.get("source_type"))
        available = _available_model_ids()

        if mode == MODE_MANUAL:
            config = _load_config(camera)
            requested = config.get("model_ids") or []
            if isinstance(requested, str):
                requested = [requested]
            selected = []
            rejected = []
            for model_id in requested:
                if model_id not in available:
                    rejected.append({"model_id": model_id, "reason": "MODEL_UNAVAILABLE"})
                elif not supports_input(model_id, input_family):
                    rejected.append({"model_id": model_id, "reason": "INPUT_INCOMPATIBLE"})
                else:
                    selected.append(model_id)
            return {
                "mode": mode,
                "scene": scene,
                "scene_confidence": scene_confidence,
                "input_family": input_family,
                "model_ids": selected,
                "rejected": rejected,
                "reason": "Operator-pinned models (filtered by availability and input compatibility).",
            }

        # AUTOMATIC: every available model that is a compatible candidate.
        selected = []
        for model_id in sorted(available):
            if is_candidate_for_scene(model_id, scene, input_family):
                selected.append(model_id)
        return {
            "mode": mode,
            "scene": scene,
            "scene_confidence": scene_confidence,
            "input_family": input_family,
            "model_ids": selected,
            "rejected": [],
            "reason": f"Scene '{scene}' selects all compatible available models.",
        }

    def evaluate(self, camera: dict, scene: str, scene_confidence: float = 1.0, force: bool = False) -> dict:
        """Evaluate + cache the model decision for a camera (hysteresis-aware).

        Automatic scene switching requires ``STABLE_SCENES_REQUIRED`` consecutive
        frames of the same scene before the active scene changes. Manual mode is
        evaluated directly.
        """
        camera_id = camera["id"]
        now = time.monotonic()

        if not force:
            last = self._last_eval.get(camera_id, 0.0)
            if now - last < RE_EVAL_INTERVAL_SECONDS:
                cached = self._last_decision.get(camera_id)
                if cached:
                    return cached

        self._last_eval[camera_id] = now
        mode = self.get_mode(camera)

        if mode == MODE_MANUAL:
            decision = self.select_models(camera, scene, scene_confidence)
        else:
            decision = self._evaluate_automatic(camera, scene, scene_confidence)

        decision["camera_id"] = camera_id
        decision["evaluated_at"] = _now_iso()
        with self._lock:
            self._last_decision[camera_id] = decision
        return decision

    def _evaluate_automatic(self, camera: dict, scene: str, scene_confidence: float) -> dict:
        camera_id = camera["id"]
        with self._lock:
            stability = self._scene_stability.setdefault(camera_id, {"scene": None, "count": 0})
            previous = self._last_decision.get(camera_id)
            active_scene = previous.get("scene") if previous else None

            if scene == active_scene:
                stability = {"scene": scene, "count": STABLE_SCENES_REQUIRED}
            else:
                if stability.get("scene") == scene:
                    stability["count"] = stability.get("count", 0) + 1
                else:
                    stability = {"scene": scene, "count": 1}
            self._scene_stability[camera_id] = stability

            # Switch only after the candidate scene has been stable long enough,
            # or when there is no prior decision yet.
            if active_scene is None or stability["count"] >= STABLE_SCENES_REQUIRED:
                resolved_scene = scene
            else:
                resolved_scene = active_scene

        return self.select_models(camera, resolved_scene, scene_confidence)

    # --------------------------------------------------------------- reporting
    def last_decision(self, camera_id: str) -> dict | None:
        with self._lock:
            return self._last_decision.get(camera_id)

    def model_ids_for(self, camera: dict, scene: str, scene_confidence: float = 1.0) -> list:
        """Convenience: just the selected model id list for a camera."""
        return self.evaluate(camera, scene, scene_confidence)["model_ids"]

    def reset(self, camera_id: str = None) -> None:
        """Clear cached decisions (used on camera stop / tests)."""
        with self._lock:
            if camera_id is None:
                self._last_decision.clear()
                self._scene_stability.clear()
                self._last_eval.clear()
            else:
                self._last_decision.pop(camera_id, None)
                self._scene_stability.pop(camera_id, None)
                self._last_eval.pop(camera_id, None)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


mode_manager = ModeManager()