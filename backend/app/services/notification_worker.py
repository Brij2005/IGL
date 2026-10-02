"""Background worker for queued SMTP and WhatsApp notifications."""
from __future__ import annotations

import logging
import threading
from typing import Any

try:
    from app.database import SessionLocal
    from app.services.notification_delivery import deliver_due_notifications
except ImportError:  # pragma: no cover
    from backend.app.database import SessionLocal
    from backend.app.services.notification_delivery import deliver_due_notifications

logger = logging.getLogger("igl.notifications.worker")


class NotificationDeliveryWorker:
    def __init__(self, session_factory=SessionLocal, interval_seconds: float = 2.0) -> None:
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = "STOPPED"
        self.last_error_type: str | None = None
        self.last_cycle: dict[str, Any] | None = None
        self.cycles_completed = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.status = "RUNNING"
        self._thread = threading.Thread(target=self._run, name="notification-delivery", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.status = "STOPPED" if not self._thread or not self._thread.is_alive() else "STOP_TIMEOUT"

    def run_cycle(self) -> dict[str, int]:
        db = self.session_factory()
        try:
            self.last_cycle = deliver_due_notifications(db)
            self.cycles_completed += 1
            self.last_error_type = None
            return self.last_cycle
        finally:
            db.close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as exc:
                self.last_error_type = type(exc).__name__
                logger.error("Notification delivery cycle failed", extra={"component": "notification_worker", "error_type": type(exc).__name__})
            self._stop_event.wait(self.interval_seconds)
        self.status = "STOPPED"


notification_delivery_worker = NotificationDeliveryWorker()
