import React, { useEffect, useState } from "react";
import { Camera, RefreshCw, Radio, Cpu, AlertTriangle, Wifi, WifiOff } from "lucide-react";
import {
  fetchCameraCommandCenterApi,
  setCameraModeApi,
  reselectCameraModelsApi
} from "../services/api";
import {
  PageHeader,
  Panel,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonSecondary,
  buttonPrimary
} from "../components/OperationsUI";

const MODE_STYLES = {
  AUTOMATIC: "bg-sky-50 text-sky-700 border-sky-200",
  MANUAL: "bg-violet-50 text-violet-700 border-violet-200"
};

function ModePill({ mode }) {
  const cls = MODE_STYLES[mode] || MODE_STYLES.AUTOMATIC;
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold ${cls}`}>{mode}</span>;
}

export default function CameraCommandCenterView() {
  const [center, setCenter] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    setError("");
    const result = await fetchCameraCommandCenterApi();
    if (!result || result.detail) {
      setError(result?.detail || "Failed to load cameras.");
      return;
    }
    setCenter(result);
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleMode = async (cameraId, currentMode) => {
    setBusy(cameraId);
    const next = currentMode === "AUTOMATIC" ? "MANUAL" : "AUTOMATIC";
    const result = await setCameraModeApi(cameraId, next);
    if (!result || result.detail) setError(result?.detail || "Mode switch failed");
    await load();
    setBusy("");
  };

  const reselect = async (cameraId) => {
    setBusy(cameraId);
    await reselectCameraModelsApi(cameraId);
    await load();
    setBusy("");
  };

  const cameras = center?.cameras || [];
  const summary = center?.summary || {};

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Camera Command Centre"
        description="Every camera with its live status, current scene, active AI models, active detections and health. A camera is only shown online when it is genuinely streaming."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">
        {[
          ["Registered", summary.registered, Camera],
          ["Online", summary.online, Wifi],
          ["Offline", summary.offline, WifiOff],
          ["Workers", summary.workers_running, Radio],
          ["Automatic", summary.automatic, Cpu],
          ["Manual", summary.manual, Cpu]
        ].map(([label, value, Icon]) => (
          <div key={label} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
            <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase text-slate-500"><Icon className="w-3.5 h-3.5" />{label}</div>
            <div className="mt-1 text-2xl font-extrabold text-slate-900">{value ?? "-"}</div>
          </div>
        ))}
      </div>

      {cameras.length === 0 ? (
        <EmptyState message="No cameras registered. Add one in Camera Management." />
      ) : (
        <div className="grid lg:grid-cols-2 gap-4">
          {cameras.map((camera) => {
            const isOnline = (camera.status || "").toUpperCase() === "ONLINE";
            return (
              <Panel
                key={camera.camera_id}
                title={camera.name}
                description={`${camera.camera_id} · ${camera.source_type}`}
                actions={<div className="flex items-center gap-2">
                  <StatusBadge status={camera.status} />
                  <ModePill mode={camera.mode} />
                </div>}
              >
                <div className="space-y-3">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2">
                      <div className="text-[9px] font-bold uppercase text-slate-500">Scene</div>
                      <div className="font-bold text-slate-800">{camera.scene || "—"}</div>
                      <div className="text-[9px] text-slate-400">{Math.round((camera.scene_confidence || 0) * 100)}% conf</div>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2">
                      <div className="text-[9px] font-bold uppercase text-slate-500">Frames</div>
                      <div className="font-bold text-slate-800">{camera.frames_processed || 0}</div>
                      <div className="text-[9px] text-slate-400">processed</div>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2">
                      <div className="text-[9px] font-bold uppercase text-slate-500">Active alerts</div>
                      <div className={`font-bold ${camera.active_alert_count > 0 ? "text-red-600" : "text-slate-800"}`}>{camera.active_alert_count || 0}</div>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2">
                      <div className="text-[9px] font-bold uppercase text-slate-500">Switches</div>
                      <div className="font-bold text-slate-800">{camera.scene_switch_count || 0}</div>
                      <div className="text-[9px] text-slate-400">scene changes</div>
                    </div>
                  </div>

                  <div>
                    <div className="text-[10px] font-bold uppercase text-slate-500 mb-1">Active models</div>
                    {camera.active_model_ids?.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {camera.active_model_ids.map((m) => (
                          <span key={m} className="inline-flex items-center gap-1 rounded bg-sky-50 border border-sky-200 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700">
                            <Cpu className="w-3 h-3" />{m}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <div className="text-[10px] text-slate-400">{isOnline ? "No compatible available model for the current scene." : "Camera is not streaming."}</div>
                    )}
                  </div>

                  {camera.scene_reason && (
                    <div className="text-[10px] text-slate-500"><span className="font-bold">Scene reason:</span> {camera.scene_reason}</div>
                  )}

                  {camera.active_detections?.length > 0 && (
                    <div>
                      <div className="flex items-center gap-1 text-[10px] font-bold uppercase text-slate-500 mb-1">
                        <AlertTriangle className="w-3 h-3 text-amber-500" />Active detections
                      </div>
                      <div className="space-y-1">
                        {camera.active_detections.slice(0, 3).map((d) => (
                          <div key={d.id} className="flex items-center justify-between rounded bg-amber-50 border border-amber-200 px-2 py-1 text-[10px]">
                            <span className="font-semibold text-amber-800">{d.event_type}</span>
                            <span className="text-slate-500">{d.model_name || d.model_id} · {Math.round((d.confidence || 0) * 100)}%</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {camera.last_error && (
                    <div className="rounded bg-red-50 border border-red-200 px-2 py-1 text-[10px] text-red-700">
                      <span className="font-bold">Worker error:</span> {camera.last_error}
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2 pt-1">
                    <button className={buttonPrimary} onClick={() => toggleMode(camera.camera_id, camera.mode)} disabled={busy === camera.camera_id}>
                      Switch to {camera.mode === "AUTOMATIC" ? "Manual" : "Automatic"}
                    </button>
                    <button className={buttonSecondary} onClick={() => reselect(camera.camera_id)} disabled={busy === camera.camera_id}>
                      <RefreshCw className="w-3.5 h-3.5 inline mr-1" />Re-select models
                    </button>
                  </div>
                </div>
              </Panel>
            );
          })}
        </div>
      )}
    </div>
  );
}