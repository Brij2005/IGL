"""Background evaluation of escalation policies over persisted events.

The worker only reads and writes rows that already exist. It never invents an
event, and it only creates an escalation when an operator-configured policy
matches a real open event whose response window has elapsed.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.database import SessionLocal
    from app.models import Event, User
    from app.services.escalation_engine import evaluate_event_escalation
except ImportError:  # pragma: no cover
    from backend.app.config import settings
    from backend.app.database import SessionLocal
    from backend.app.models import Event, User
    from backend.app.services.escalation_engine import evaluate_event_escalation


logger = logging.getLogger("igl.safety.event_worker")

OPEN_WORKFLOW_STATES = ("NEW", "UNACKNOWLEDGED", "ACKNOWLEDGED", "ASSIGNED", "UNDER_INVESTIGATION", "ACTION_REQUIRED")


class SafetyEventWorker:
    def __init__(self, session_factory=SessionLocal, interval_seconds: float | None = None) -> None:
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds or settings.SAFETY_EVENT_WORKER_INTERVAL_SECONDS
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self.status = "STOPPED"
        self.last_cycle_summary: Dict[str, Any] | None = None
        self.last_error_type: str | None = None
        self.cycles_completed = 0

    def start(self) -> None:
        if not settings.SAFETY_EVENT_WORKER_ENABLED:
            # Disabled by configuration is reported as disabled, not started.
            with self._state_lock:
                self.status = "NOT_CONFIGURED"
            return
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self.status = "RUNNING"
            self._thread = threading.Thread(target=self._run, name="safety-event-worker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._state_lock:
            self.status = "STOPPED" if not thread or not thread.is_alive() else "STOP_TIMEOUT"

    def run_cycle(self, db: Session | None = None, now: datetime | None = None) -> Dict[str, Any]:
        """Evaluate every open event once and report what the policies decided."""
        owns_session = db is None
        session = db or self.session_factory()
        reference = now or datetime.now(timezone.utc)
        summary: Dict[str, Any] = {
            "evaluated_at": reference,
            "events_considered": 0,
            "events_escalated": 0,
            "events_not_due": 0,
            "events_without_policy": 0,
            "events_already_handled": 0,
            "escalations_created": 0,
        }
        try:
            events: List[Event] = (
                session.query(Event)
                .filter(Event.workflow_state.in_(OPEN_WORKFLOW_STATES), Event.ended_at.is_(None))
                .order_by(Event.started_at)
                .limit(500)
                .all()
            )
            summary["events_considered"] = len(events)
            for event in events:
                report = evaluate_event_escalation(session, event, now=reference)
                if report["status"] == "ESCALATED":
                    summary["events_escalated"] += 1
                    summary["escalations_created"] += len(report["escalations_created"])
                elif report["status"] == "NOT_DUE":
                    summary["events_not_due"] += 1
                elif report["status"] == "ESCALATION_NOT_CONFIGURED":
                    summary["events_without_policy"] += 1
                elif report["status"] == "EVENT_ALREADY_HANDLED":
                    summary["events_already_handled"] += 1
            self.last_cycle_summary = summary
            self.cycles_completed += 1
            self.last_error_type = None
            return summary
        finally:
            if owns_session:
                session.close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as exc:
                self.last_error_type = type(exc).__name__
                logger.error(
                    "Safety event cycle failed",
                    extra={"component": "safety_event_worker", "error_type": type(exc).__name__},
                )
            self._stop_event.wait(self.interval_seconds)
        with self._state_lock:
            self.status = "STOPPED"


safety_event_worker = SafetyEventWorker()