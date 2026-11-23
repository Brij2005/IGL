import React, { useEffect, useState } from "react";
import { Droplets, RefreshCw, ShieldCheck, ShieldX } from "lucide-react";
import { fetchLeakageIncidentsApi, fetchModelEvaluationApi, fetchModelsApi, reviewLeakageIncidentApi } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonPrimary,
  buttonSecondary,
  buttonDanger
} from "../components/OperationsUI";

export default function LeakageView() {
  const [incidents, setIncidents] = useState([]);
  const [models, setModels] = useState([]);
  const [evaluation, setEvaluation] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    setError("");
    const [incidentResult, modelResult, evaluationResult] = await Promise.all([
      fetchLeakageIncidentsApi(),
      fetchModelsApi(),
      fetchModelEvaluationApi("model-water-leakage")
    ]);
    setIncidents(incidentResult || []);
    setModels(modelResult || []);
    setEvaluation(evaluationResult || null);
  };

  useEffect(() => {
    load();
  }, []);

  const review = async (incident, decision) => {
    const notes = window.prompt(`Review notes for ${incident.id} (${decision})`, "");
    if (notes === null) return;
    setBusy(`${incident.id}:${decision}`);
    const result = await reviewLeakageIncidentApi(incident.id, decision, notes);
    if (!result || result.detail) setError(result?.detail || "Review failed");
    await load();
    setBusy("");
  };

  const leakageModel = models.find((model) => model.id === "model-water-leakage");
  const columns = [
    { key: "id", label: "Incident" },
    { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
    { key: "confidence", label: "Confidence", render: (row) => `${Math.round((row.confidence || 0) * 100)}%` },
    { key: "camera_id", label: "Camera" },
    { key: "pipeline_id", label: "Pipeline", render: (row) => row.pipeline_id || "-" },
    { key: "detected_at", label: "Detected", render: (row) => new Date(row.detected_at).toLocaleString() },
    { key: "actions", label: "Authorized review", render: (row) => (
      <div className="flex flex-wrap gap-1.5">
        <button className={buttonSecondary} onClick={() => review(row, "CONFIRMED")} disabled={busy === `${row.id}:CONFIRMED`}><ShieldCheck className="w-3.5 h-3.5 inline mr-1" />Confirm</button>
        <button className={buttonSecondary} onClick={() => review(row, "SUSPECTED")} disabled={busy === `${row.id}:SUSPECTED`}>Suspect</button>
        <button className={buttonDanger} onClick={() => review(row, "DISMISSED")} disabled={busy === `${row.id}:DISMISSED`}><ShieldX className="w-3.5 h-3.5 inline mr-1" />Dismiss</button>
      </div>
    ) }
  ];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Water Pipeline Leakage"
        description="Leakage incidents are created from genuine model output and temporal verification. Water accumulation alone is never automatically promoted to a confirmed pipe leakage. Only authorized review can confirm."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <div className="grid lg:grid-cols-[1.5fr_1fr] gap-4 mb-4">
        <Panel title="Leakage incidents" description="Possible and suspected incidents remain reviewable. Confirmed incidents are only created by a human decision.">
          {incidents.length === 0 ? <EmptyState message="No leakage incidents recorded." /> : <DataTable columns={columns} rows={incidents} rowKey={(row) => row.id} />}
        </Panel>
        <Panel title="Leakage model status" description="A custom leakage model is required. Generic COCO weights are not used as a leakage detector.">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-center gap-2">
              <Droplets className="w-5 h-5 text-sky-600" />
              <div className="text-sm font-bold text-slate-800">{leakageModel?.name || "Water Pipeline Leakage"}</div>
            </div>
            <div className="mt-2"><StatusBadge status={leakageModel?.status || "NOT_TRAINED"} /></div>
            <p className="mt-2 text-[11px] text-slate-500">
              Active version: <span className="font-bold">{evaluation?.active_version || leakageModel?.active_version || "-"}</span>
            </p>
            {evaluation?.per_class?.length > 0 && (
              <div className="mt-2 grid grid-cols-2 gap-1.5">
                {evaluation.per_class.map((row) => (
                  <div key={row.class_name} className="rounded border border-slate-200 bg-white px-2 py-1">
                    <div className="text-[9px] font-semibold text-slate-600 truncate">{row.class_name}</div>
                    <div className="text-[10px] font-bold text-slate-800">mAP50 {Number(row.mAP50 || 0).toFixed(3)}</div>
                  </div>
                ))}
              </div>
            )}
            <p className="mt-2 text-[11px] text-slate-500">
              {leakageModel?.status === "AVAILABLE"
                ? "A trained model is registered and can be used by the event engine. Weak classes are shown explicitly in Models & Datasets."
                : "No trained leakage weights are registered."}
            </p>
            <p className="mt-2 text-[10px] text-slate-400">
              Dataset source researched: Roboflow Universe water leakage / pipeline leakage exports (CC BY 4.0). Exporting a public dataset requires a Roboflow API key.
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}