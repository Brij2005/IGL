import React, { useEffect, useState } from "react";
import { RefreshCw, AlertTriangle, Image as ImageIcon } from "lucide-react";
import { fetchCentralDetectionsApi, fetchCameras, evidenceUrl } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonSecondary
} from "../components/OperationsUI";

function EvidenceCell({ row }) {
  if (!row.evidence_id) return <span className="text-[10px] text-slate-400">—</span>;
  return (
    <a href={evidenceUrl(row.evidence_id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-600 hover:underline">
      <ImageIcon className="w-3.5 h-3.5" />
      View
    </a>
  );
}

export default function DetectionCenterView() {
  const [detections, setDetections] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [cameraId, setCameraId] = useState("");
  const [eventType, setEventType] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    setError("");
    const result = await fetchCentralDetectionsApi({ limit: 100, cameraId: cameraId || undefined, eventType: eventType || undefined });
    if (!result) setError("Failed to load detections.");
    else setDetections(result);
    setBusy(false);
  };

  useEffect(() => {
    fetchCameras().then(setCameras);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraId, eventType]);

  const eventTypes = Array.from(new Set(detections.map((d) => d.event_type))).filter(Boolean);

  const columns = [
    {
      key: "event_type",
      label: "Event",
      render: (row) => (
        <div className="flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
          <div>
            <div className="font-semibold text-slate-800">{row.event_type}</div>
            <div className="text-[10px] text-slate-400">{row.severity}</div>
          </div>
        </div>
      )
    },
    { key: "camera_name", label: "Camera", render: (row) => row.camera_name || row.camera_id },
    { key: "model_name", label: "Model", render: (row) => row.model_name || row.model_id || "—" },
    { key: "confidence", label: "Confidence", render: (row) => `${Math.round((row.confidence || 0) * 100)}%` },
    { key: "evidence", label: "Evidence", render: (row) => <EvidenceCell row={row} /> },
    { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
    { key: "created_at", label: "Time", render: (row) => new Date(row.created_at).toLocaleString() }
  ];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Detection & Alerts"
        description="Centralized feed of genuine detection events from every model, with camera, model, timestamp, confidence and evidence. Untrained or unavailable models never appear here."
        actions={<button className={buttonSecondary} onClick={load} disabled={busy}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />{busy ? "Loading..." : "Refresh"}</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <Panel title="Filters" description="Filter the centralized detection feed by camera and event type." className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs">
            <span className="font-semibold text-slate-600">Camera</span>
            <select className="mt-1 block rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs" value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">All cameras</option>
              {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="text-xs">
            <span className="font-semibold text-slate-600">Event type</span>
            <select className="mt-1 block rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs" value={eventType} onChange={(e) => setEventType(e.target.value)}>
              <option value="">All types</option>
              {eventTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        </div>
      </Panel>

      <Panel title="Detections" description={`Showing ${detections.length} recent detection event(s).`}>
        {detections.length === 0
          ? <EmptyState message="No detection events yet. Start a camera with a compatible available model to generate genuine detections." />
          : <DataTable rows={detections} rowKey={(row) => row.id} columns={columns} />
        }
      </Panel>
    </div>
  );
}