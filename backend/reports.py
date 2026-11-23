import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import REPORTS_DIR
from database import get_db_connection, now_iso


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _period(report_type, start=None, end=None):
    now = datetime.now(timezone.utc)
    if report_type == "daily":
        start = start or now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end or start + timedelta(days=1)
    elif report_type == "weekly":
        start = start or (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = end or start + timedelta(days=7)
    elif report_type == "monthly":
        start = start or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = end or (start + timedelta(days=32)).replace(day=1)
    else:
        start = start or now - timedelta(days=30)
        end = end or now
    return _iso(start), _iso(end)


def _query(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def build_metrics(report_type, start, end):
    conn = get_db_connection()
    alert_count = _query(
        conn,
        "SELECT COUNT(*) FROM alerts WHERE detected_at >= ? AND detected_at < ?",
        (start, end),
    )[0][0]
    by_type = _query(
        conn,
        "SELECT event_type, COUNT(*) FROM alerts WHERE detected_at >= ? AND detected_at < ? GROUP BY event_type ORDER BY COUNT(*) DESC",
        (start, end),
    )
    by_severity = _query(
        conn,
        "SELECT severity, COUNT(*) FROM alerts WHERE detected_at >= ? AND detected_at < ? GROUP BY severity",
        (start, end),
    )
    leakage = _query(
        conn,
        "SELECT status, COUNT(*) FROM leakage_incidents WHERE detected_at >= ? AND detected_at < ? GROUP BY status",
        (start, end),
    )
    safety = _query(
        conn,
        "SELECT status, COUNT(*) FROM safety_incidents WHERE detected_at >= ? AND detected_at < ? GROUP BY status",
        (start, end),
    )
    cameras_total = _query(conn, "SELECT COUNT(*) FROM cameras")[0][0]
    cameras_online = _query(conn, "SELECT COUNT(*) FROM cameras WHERE status = 'ONLINE'")[0][0]
    evidence_count = _query(
        conn,
        "SELECT COUNT(*) FROM evidence_files WHERE created_at >= ? AND created_at < ?",
        (start, end),
    )[0][0]
    deliveries = _query(
        conn,
        "SELECT status, COUNT(*) FROM notification_deliveries WHERE sent_at >= ? AND sent_at < ? GROUP BY status",
        (start, end),
    )
    models = _query(conn, "SELECT id, name, status, metrics_json FROM ai_models ORDER BY name")
    conn.close()
    return {
        "report_type": report_type,
        "period_start": start,
        "period_end": end,
        "alerts": {
            "total": alert_count,
            "by_type": [{"event_type": row[0], "count": row[1]} for row in by_type],
            "by_severity": [{"severity": row[0], "count": row[1]} for row in by_severity],
        },
        "leakage_incidents": [{"status": row[0], "count": row[1]} for row in leakage],
        "safety_incidents": [{"status": row[0], "count": row[1]} for row in safety],
        "cameras": {"total": cameras_total, "online": cameras_online},
        "evidence_files": evidence_count,
        "notifications": [{"status": row[0], "count": row[1]} for row in deliveries],
        "models": [
            {
                "id": row[0],
                "name": row[1],
                "status": row[2],
                "metrics": json.loads(row[3] or "{}"),
            }
            for row in models
        ],
    }


def generate_report(report_type, start=None, end=None, generated_by="system", format="json"):
    start, end = _period(report_type, start, end)
    metrics = build_metrics(report_type, start, end)
    report_id = f"RPT-{uuid.uuid4().hex[:10].upper()}"
    file_path = None
    if format in {"json", "csv"}:
        target = Path(REPORTS_DIR) / f"{report_id}.{format}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if format == "json":
            target.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        else:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["metric", "value"])
            writer.writerow(["report_type", report_type])
            writer.writerow(["period_start", start])
            writer.writerow(["period_end", end])
            writer.writerow(["alerts_total", metrics["alerts"]["total"]])
            writer.writerow(["cameras_total", metrics["cameras"]["total"]])
            writer.writerow(["cameras_online", metrics["cameras"]["online"]])
            writer.writerow(["evidence_files", metrics["evidence_files"]])
            target.write_text(buffer.getvalue(), encoding="utf-8")
        file_path = str(target)
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO reports (id, report_type, period_start, period_end, format, file_path, generated_by, metrics_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (report_id, report_type, start, end, format, file_path, generated_by, json.dumps(metrics)),
    )
    conn.commit()
    conn.close()
    return {"report_id": report_id, "file_path": file_path, **metrics}


def list_reports(limit=100):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_report(report_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    conn.close()
    return dict(row) if row else None