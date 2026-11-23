import React, { useEffect, useState } from "react";
import { Camera, Play, Square, Trash2, RefreshCw, ExternalLink } from "lucide-react";
import {
  deleteCameraApi,
  fetchCameras,
  registerCameraApi,
  startCameraApi,
  stopCameraApi,
  testCameraApi,
  cameraStreamUrl
} from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  StatusBadge,
  EmptyState,
  ErrorState,
  FormField,
  inputClass,
  buttonPrimary,
  buttonSecondary,
  buttonDanger
} from "../components/OperationsUI";

export default function CameraManagementView() {
  const [cameras, setCameras] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", source_type: "rtsp", source_url: "", username: "", password: "" });
  const [busy, setBusy] = useState("");

  const load = async () => {
    setLoading(true);
    const result = await fetchCameras();
    setCameras(result || []);
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, []);

  const register = async () => {
    if (!form.name.trim() || !form.source_url.trim()) {
      setError("Camera name and source URL are required.");
      return;
    }
    setError("");
    const result = await registerCameraApi(form);
    if (!result || result.status !== "SUCCESS") {
      setError("Camera registration failed. Check the backend connection and fields.");
      return;
    }
    setForm({ name: "", source_type: "rtsp", source_url: "", username: "", password: "" });
    await load();
  };

  const action = async (cameraId, fn, label) => {
    setBusy(`${cameraId}:${label}`);
    setError("");
    const result = await fn(cameraId);
    if (!result || result.detail) setError(result?.detail || `${label} failed`);
    await load();
    setBusy("");
  };

  const columns = [
    { key: "name", label: "Camera", render: (row) => <div className="font-semibold text-slate-800">{row.name}</div> },
    { key: "source_type", label: "Source" },
    { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
    { key: "fps", label: "FPS", render: (row) => row.fps || 0 },
    { key: "actions", label: "Actions", render: (row) => (
      <div className="flex flex-wrap gap-1.5">
        <button className={buttonSecondary} onClick={() => action(row.id, startCameraApi, "start")} disabled={busy === `${row.id}:start`}>Start</button>
        <button className={buttonSecondary} onClick={() => action(row.id, stopCameraApi, "stop")} disabled={busy === `${row.id}:stop`}>Stop</button>
        <button className={buttonSecondary} onClick={() => action(row.id, testCameraApi, "test")} disabled={busy === `${row.id}:test`}>Test</button>
        <a className={buttonSecondary} href={cameraStreamUrl(row.id)} target="_blank" rel="noreferrer"><ExternalLink className="w-3.5 h-3.5" /></a>
        <button className={buttonDanger} onClick={() => {
          if (window.confirm(`Delete camera ${row.name}?`)) action(row.id, deleteCameraApi, "delete");
        }} disabled={busy === `${row.id}:delete`}>Delete</button>
      </div>
    ) },
    { key: "plant", label: "Location", render: (row) => row.plant_location_id || "-" },
    { key: "pipeline", label: "Pipeline", render: (row) => row.pipeline_id || "-" }
  ];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Camera Management"
        description="Register authorized RTSP, ONVIF, webcam, image and file sources. Real camera testing requires the source to be reachable from the backend host."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <div className="grid lg:grid-cols-[2fr_1fr] gap-4">
        <Panel title="Registered cameras" description="Status is persisted by the backend. Start monitoring to open the stream.">
          {loading ? <div className="text-xs text-slate-500">Loading cameras...</div> : cameras.length === 0 ? (
            <EmptyState message="No cameras registered. Add the first authorized source." />
          ) : <DataTable columns={columns} rows={cameras} emptyMessage="No cameras registered." rowKey={(row) => row.id} />}
        </Panel>

        <Panel title="Register a camera" description="Credentials are encrypted at rest and never returned to the frontend.">
          <div className="space-y-3">
            <FormField label="Camera name">
              <input className={inputClass} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </FormField>
            <FormField label="Source type">
              <select className={inputClass} value={form.source_type} onChange={(event) => setForm({ ...form, source_type: event.target.value })}>
                <option value="rtsp">RTSP</option>
                <option value="onvif">ONVIF / RTSP</option>
                <option value="webcam">Local webcam</option>
                <option value="file">Video file</option>
                <option value="image">Image file</option>
                <option value="http">Public HTTP video</option>
              </select>
            </FormField>
            <FormField label="Source URL / device path" hint="Use a backend-accessible path or authorized URL.">
              <input className={inputClass} value={form.source_url} onChange={(event) => setForm({ ...form, source_url: event.target.value })} />
            </FormField>
            <FormField label="Username (optional)">
              <input className={inputClass} value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} />
            </FormField>
            <FormField label="Password (optional)">
              <input className={inputClass} type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
            </FormField>
            <button className={buttonPrimary} onClick={register} disabled={busy === "register"}>Register camera</button>
            <p className="text-[10px] text-slate-400">The backend must be able to reach the camera network. Browser-only RTSP playback is not used.</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}