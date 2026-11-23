import React, { useEffect, useState } from "react";
import { Activity, Database, HeartPulse, RefreshCw, Server } from "lucide-react";
import { fetchSystemHealthApi } from "../services/api";
import {
  PageHeader,
  Panel,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonSecondary
} from "../components/OperationsUI";

export default function SystemHealthView() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");

  const load = async () => {
    const result = await fetchSystemHealthApi();
    if (!result || result.detail) setError(result?.detail || "System health request failed");
    else setHealth(result);
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="System Health"
        description="Live backend health: database connectivity, registered cameras, model load status, notification providers and evidence/report storage."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {!health ? <EmptyState message="No system health data available." /> : (
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-700"><Database className="w-4 h-4 text-sky-600" />Database</div>
            <div className="mt-2"><StatusBadge status={health.database?.connected ? "ONLINE" : "OFFLINE"} /></div>
            <p className="mt-2 text-[10px] text-slate-500 break-all">{health.database?.path}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-700"><Server className="w-4 h-4 text-emerald-600" />Cameras</div>
            <div className="mt-2 text-2xl font-extrabold">{Object.values(health.cameras || {}).reduce((sum, value) => sum + Number(value || 0), 0)}</div>
            <div className="mt-1 flex flex-wrap gap-1">{Object.entries(health.cameras || {}).map(([status, count]) => <StatusBadge key={status} status={status} />)}{Object.keys(health.cameras || {}).length === 0 && <span className="text-[10px] text-slate-400">No cameras registered</span>}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-700"><Activity className="w-4 h-4 text-amber-600" />Models</div>
            <div className="mt-2 text-2xl font-extrabold">{(health.models || []).filter((model) => model.loaded).length} / {(health.models || []).length}</div>
            <p className="mt-1 text-[10px] text-slate-500">Loaded / registered models</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-700"><HeartPulse className="w-4 h-4 text-red-500" />Auth</div>
            <div className="mt-2"><StatusBadge status={health.auth_enabled ? "ONLINE" : "DISABLED"} /></div>
            <p className="mt-2 text-[10px] text-slate-500">Authentication middleware</p>
          </div>
        </div>
      )}

      {health && (
        <div className="grid lg:grid-cols-2 gap-4 mt-4">
          <Panel title="Models" description="Loaded model status from the backend registry.">
            {(health.models || []).length === 0 ? <EmptyState message="No models registered." /> : (
              <div className="space-y-2">
                {health.models.map((model) => (
                  <div key={model.model_id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 p-2.5">
                    <div>
                      <div className="text-xs font-bold text-slate-800">{model.name}</div>
                      <div className="text-[10px] text-slate-500">{model.model_id} · {model.weights_path || "no weights"}</div>
                    </div>
                    <StatusBadge status={model.loaded ? "ONLINE" : model.status} />
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Storage & providers" description="Operational configuration status. Secrets are never returned.">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg border border-slate-200 p-3"><div className="font-bold">Evidence</div><div className="text-[10px] text-slate-500 mt-1">{health.storage?.evidence_dir}</div></div>
              <div className="rounded-lg border border-slate-200 p-3"><div className="font-bold">Reports</div><div className="text-[10px] text-slate-500 mt-1">{health.storage?.reports_dir}</div></div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {Object.entries(health.notifications || {}).map(([channel, info]) => (
                <div key={channel} className="rounded-lg border border-slate-200 p-2.5">
                  <div className="text-[11px] font-bold text-slate-700">{channel}</div>
                  <div className="mt-1"><StatusBadge status={info?.configured ? "CONFIGURED" : "NOT_CONFIGURED"} /></div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}