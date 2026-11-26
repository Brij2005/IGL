import React, { useEffect, useState } from "react";
import { RefreshCw, Save, Link2, Cpu } from "lucide-react";
import {
  fetchModelCameraMappingApi,
  fetchModelHubApi,
  setCameraManualModelsApi,
  setCameraModeApi
} from "../services/api";
import {
  PageHeader,
  Panel,
  EmptyState,
  ErrorState,
  StatusBadge,
  FormField,
  buttonPrimary,
  buttonSecondary,
  inputClass
} from "../components/OperationsUI";

export default function ModelCameraMappingView() {
  const [mapping, setMapping] = useState(null);
  const [models, setModels] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [selections, setSelections] = useState({}); // cameraId -> [modelIds]

  const load = async () => {
    setError("");
    const [mapResult, hubResult] = await Promise.all([
      fetchModelCameraMappingApi(),
      fetchModelHubApi()
    ]);
    if (!mapResult || mapResult.detail) {
      setError(mapResult?.detail || "Failed to load mapping.");
      return;
    }
    setMapping(mapResult);
    setModels(hubResult?.models || []);
    // Seed selections from current assigned models.
    const next = {};
    (mapResult.mappings || []).forEach((m) => {
      next[m.camera_id] = m.active_models?.map((am) => am.model_id) || m.assigned_model_ids || [];
    });
    setSelections(next);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleModel = (cameraId, modelId) => {
    setSelections((current) => {
      const list = current[cameraId] || [];
      return {
        ...current,
        [cameraId]: list.includes(modelId) ? list.filter((m) => m !== modelId) : [...list, modelId]
      };
    });
  };

  const saveManual = async (cameraId) => {
    setBusy(cameraId);
    const result = await setCameraManualModelsApi(cameraId, selections[cameraId] || []);
    if (!result || result.detail) setError(result?.detail || "Manual model assignment failed");
    await load();
    setBusy("");
  };

  const setAutomatic = async (cameraId) => {
    setBusy(cameraId);
    const result = await setCameraModeApi(cameraId, "AUTOMATIC");
    if (!result || result.detail) setError(result?.detail || "Failed to switch to Automatic");
    await load();
    setBusy("");
  };

  const mappings = mapping?.mappings || [];
  const availableModels = models.filter((m) => m.available);

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Model-to-Camera Mapping"
        description="See which models are active on each camera and why. Assign models manually per camera (Manual mode) or switch back to Automatic scene-based selection. Unavailable, untrained or incompatible models are never run."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      {mappings.length === 0 ? (
        <EmptyState message="No cameras to map. Register a camera first." />
      ) : (
        <div className="space-y-4">
          {mappings.map((m) => (
            <Panel
              key={m.camera_id}
              title={m.camera_name}
              description={`${m.camera_id} · ${m.source_type} · Scene: ${m.scene}`}
              actions={<div className="flex items-center gap-2">
                <span className="text-[10px] font-bold text-slate-500">{m.mode}</span>
                {m.worker_running && <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">RUNNING</span>}
              </div>}
            >
              <div className="grid lg:grid-cols-[1.1fr_1fr] gap-4">
                <div>
                  <div className="text-[10px] font-bold uppercase text-slate-500 mb-1">Active models now</div>
                  {m.active_models?.length > 0 ? (
                    <div className="space-y-1">
                      {m.active_models.map((am) => (
                        <div key={am.model_id} className="flex items-center justify-between rounded border border-slate-200 px-2 py-1 text-[11px]">
                          <span className="flex items-center gap-1 font-semibold text-slate-700"><Cpu className="w-3 h-3" />{am.model_name}</span>
                          <StatusBadge status={am.status} />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-[11px] text-slate-400">{m.worker_running ? "No compatible available model for this scene." : "Camera not running."}</div>
                  )}
                  {m.decision?.reason && (
                    <div className="mt-2 text-[10px] text-slate-500"><span className="font-bold">Selection reason:</span> {m.decision.reason}</div>
                  )}
                  {m.decision?.rejected?.length > 0 && (
                    <div className="mt-1 text-[10px] text-amber-600">
                      Rejected: {m.decision.rejected.map((r) => `${r.model_id} (${r.reason})`).join(", ")}
                    </div>
                  )}
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <FormField label="Assign models manually (Manual mode)">
                    <div className="space-y-1 max-h-48 overflow-y-auto">
                      {availableModels.length === 0 ? (
                        <div className="text-[10px] text-slate-400">No available models.</div>
                      ) : availableModels.map((model) => {
                        const checked = (selections[m.camera_id] || []).includes(model.model_id);
                        return (
                          <label key={model.model_id} className="flex items-center gap-2 text-[11px] text-slate-700 cursor-pointer">
                            <input
                              type="checkbox"
                              className="rounded border-slate-300"
                              checked={checked}
                              onChange={() => toggleModel(m.camera_id, model.model_id)}
                            />
                            <span className="font-semibold">{model.display_name || model.name}</span>
                            <span className="text-[9px] text-slate-400">{(model.inputs || []).join("/")}</span>
                          </label>
                        );
                      })}
                    </div>
                  </FormField>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button className={buttonPrimary} onClick={() => saveManual(m.camera_id)} disabled={busy === m.camera_id}>
                      <Save className="w-3.5 h-3.5 inline mr-1" />Save (Manual)
                    </button>
                    <button className={buttonSecondary} onClick={() => setAutomatic(m.camera_id)} disabled={busy === m.camera_id}>
                      <Link2 className="w-3.5 h-3.5 inline mr-1" />Use Automatic
                    </button>
                  </div>
                  <div className="mt-2 text-[9px] text-slate-400">
                    Unavailable, untrained and input-incompatible models are automatically excluded at run time.
                  </div>
                </div>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}