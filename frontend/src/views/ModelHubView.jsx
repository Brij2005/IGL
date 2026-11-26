import React, { useEffect, useState } from "react";
import { Cpu, RefreshCw, CheckCircle2, AlertTriangle, XCircle, Camera } from "lucide-react";
import {
  fetchModelHubApi,
  reloadModelApi,
  activateModelVersionApi
} from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonSecondary
} from "../components/OperationsUI";

const STATUS_STYLES = {
  AVAILABLE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  NOT_TRAINED: "bg-amber-50 text-amber-700 border-amber-200",
  NOT_DEPLOYED: "bg-slate-50 text-slate-600 border-slate-200"
};

function StatusPill({ status }) {
  const cls = STATUS_STYLES[status] || STATUS_STYLES.NOT_DEPLOYED;
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold ${cls}`}>{status}</span>;
}

export default function ModelHubView() {
  const [hub, setHub] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    setError("");
    const result = await fetchModelHubApi();
    if (!result || result.detail) {
      setError(result?.detail || "Failed to load the AI Model Hub.");
      return;
    }
    setHub(result);
  };

  useEffect(() => {
    load();
  }, []);

  const reload = async (modelId) => {
    setBusy(modelId);
    const result = await reloadModelApi(modelId);
    if (!result || result.detail) setError(result?.detail || "Model reload failed");
    await load();
    setBusy("");
  };

  const activateVersion = async (modelId, version) => {
    setBusy(`${modelId}:${version}`);
    const result = await activateModelVersionApi(modelId, version);
    if (!result || result.detail) setError(result?.detail || "Version activation failed");
    await load();
    setBusy("");
  };

  const models = hub?.models || [];
  const summary = hub?.summary || {};

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="AI Model Hub"
        description="Centralized registry of every AI model: status, loading state, versions, supported classes and where each model is used. Untrained models are marked NOT_TRAINED and unavailable models are never run."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        {[
          ["Total models", summary.total, Cpu],
          ["Available", summary.available, CheckCircle2],
          ["Untrained", summary.untrained, AlertTriangle],
          ["Unavailable", summary.unavailable, XCircle],
          ["Loaded", summary.loaded, Cpu]
        ].map(([label, value, Icon]) => (
          <div key={label} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
            <div className="flex items-center gap-2 text-[10px] font-bold uppercase text-slate-500"><Icon className="w-3.5 h-3.5" />{label}</div>
            <div className="mt-1 text-2xl font-extrabold text-slate-900">{value ?? "-"}</div>
          </div>
        ))}
      </div>

      <Panel title="Registered models" description="Availability, loading state, supported classes and camera usage for every registered model.">
        {models.length === 0 ? <EmptyState message="No models registered." /> : (
          <div className="space-y-3">
            {models.map((model) => (
              <div key={model.model_id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-bold text-slate-800">{model.display_name || model.name}</span>
                      <StatusPill status={model.status} />
                      {model.loaded ? (
                        <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">LOADED</span>
                      ) : (
                        <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-bold text-slate-500">NOT LOADED</span>
                      )}
                      {model.active_version && <span className="text-[10px] font-bold text-sky-700">v{model.active_version}</span>}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-1">{model.purpose}</div>
                    <div className="text-[10px] text-slate-400 mt-0.5">
                      Inputs: {(model.inputs || []).join(", ") || "—"} · Scenes: {(model.scenes || []).join(", ") || "—"}
                    </div>
                  </div>
                  <button className={buttonSecondary} onClick={() => reload(model.model_id)} disabled={busy === model.model_id || !model.available}>
                    <RefreshCw className="w-3.5 h-3.5 inline mr-1" />{busy === model.model_id ? "Reloading..." : "Reload"}
                  </button>
                </div>

                <div className="mt-2 grid md:grid-cols-2 gap-3">
                  <div>
                    <div className="text-[10px] font-bold uppercase text-slate-500 mb-1">Supported classes</div>
                    {model.classes && Object.keys(model.classes).length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(model.classes).slice(0, 12).map(([key, value]) => (
                          <span key={key} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{value}</span>
                        ))}
                      </div>
                    ) : <div className="text-[10px] text-slate-400">No classes registered.</div>}
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase text-slate-500 mb-1">Model-to-camera usage</div>
                    {model.used_by_cameras?.length > 0 ? (
                      <div className="space-y-0.5">
                        {model.used_by_cameras.map((usage) => (
                          <div key={usage.camera_id} className="flex items-center gap-1 text-[10px] text-slate-600">
                            <Camera className="w-3 h-3 text-slate-400" /><span className="font-semibold">{usage.camera_name}</span>
                          </div>
                        ))}
                      </div>
                    ) : <div className="text-[10px] text-slate-400">Not assigned to any camera.</div>}
                  </div>
                </div>

                {model.versions?.length > 0 && (
                  <div className="mt-3">
                    <div className="text-[10px] font-bold uppercase text-slate-500 mb-1">Version history</div>
                    <DataTable
                      rows={model.versions}
                      rowKey={(row) => row.version}
                      columns={[
                        { key: "version", label: "Version" },
                        { key: "weights_path", label: "Weights" },
                        { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
                        {
                          key: "actions",
                          label: "Actions",
                          render: (row) => (
                            row.version !== model.active_version && row.status === "COMPLETED" ? (
                              <button
                                className={buttonSecondary}
                                onClick={() => activateVersion(model.model_id, row.version)}
                                disabled={busy === `${model.model_id}:${row.version}`}
                              >
                                Activate
                              </button>
                            ) : <span className="text-[10px] text-slate-400">{row.version === model.active_version ? "Active" : "—"}</span>
                          )
                        }
                      ]}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Model status reference" description="Status values are honest: untrained or unavailable models are never selected automatically." className="mt-4">
        <div className="grid md:grid-cols-3 gap-3 text-[11px] text-slate-600">
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
            <StatusPill status="AVAILABLE" />
            <p className="mt-1">Trained, weights present and deployable. Eligible for automatic scene-based selection.</p>
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <StatusPill status="NOT_TRAINED" />
            <p className="mt-1">Registered but no trained weights. Never run and never generates detections or alerts.</p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <StatusPill status="NOT_DEPLOYED" />
            <p className="mt-1">Configured but not deployed. Not eligible for automatic selection.</p>
          </div>
        </div>
      </Panel>
    </div>
  );
}