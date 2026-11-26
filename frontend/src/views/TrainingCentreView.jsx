import React, { useEffect, useState } from "react";
import { RefreshCw, Cpu, Database, CheckCircle2, AlertTriangle, XCircle, PlayCircle } from "lucide-react";
import {
  fetchModelHubApi,
  fetchDatasetsApi,
  fetchModelEvaluationApi
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

export default function TrainingCentreView() {
  const [hub, setHub] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [evaluation, setEvaluation] = useState(null);
  const [selectedModel, setSelectedModel] = useState("model-water-leakage");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    const [hubResult, datasetResult] = await Promise.all([fetchModelHubApi(), fetchDatasetsApi()]);
    if (!hubResult || hubResult.detail) {
      setError(hubResult?.detail || "Failed to load model hub.");
      return;
    }
    setHub(hubResult);
    setDatasets(datasetResult || []);
    const evalResult = await fetchModelEvaluationApi(selectedModel);
    setEvaluation(evalResult || null);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel]);

  const models = hub?.models || [];
  const activeModel = models.find((m) => m.model_id === selectedModel);
  const perClass = evaluation?.per_class || [];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Model Training Centre"
        description="Training, validation and deployment status for every registered model. Metrics come from genuine evaluation runs; untrained models are shown as NOT_TRAINED and never produce detections."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <div className="grid xl:grid-cols-2 gap-4">
        <Panel title="Training & deployment status" description="Each model's lifecycle status, active version and loading state.">
          {models.length === 0 ? <EmptyState message="No models registered." /> : (
            <div className="space-y-2">
              {models.map((model) => (
                <button
                  key={model.model_id}
                  onClick={() => setSelectedModel(model.model_id)}
                  className={`w-full text-left rounded-lg border p-3 transition ${selectedModel === model.model_id ? "border-sky-400 bg-sky-50" : "border-slate-200 hover:bg-slate-50"}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {model.status === "AVAILABLE" ? <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                        : model.status === "NOT_TRAINED" ? <AlertTriangle className="w-4 h-4 text-amber-500" />
                        : <XCircle className="w-4 h-4 text-slate-400" />}
                      <span className="text-sm font-bold text-slate-800">{model.display_name || model.name}</span>
                    </div>
                    <StatusBadge status={model.status} />
                  </div>
                  <div className="mt-1 text-[10px] text-slate-500">
                    {model.active_version ? `Active v${model.active_version} · ` : ""}{model.weights_path || "No weights"}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          title="Active model evaluation"
          description="Independent test metrics for the selected model."
          actions={<select className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs" value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
            {models.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name || m.name}</option>)}
          </select>}
        >
          {activeModel ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={activeModel.status} />
                <span className="text-xs font-bold text-slate-700">Version: {evaluation?.active_version || activeModel.active_version || "—"}</span>
                <span className="text-[10px] text-slate-500">{evaluation?.weights_path || activeModel.weights_path || ""}</span>
              </div>
              {activeModel.status === "NOT_TRAINED" ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-800">
                  <div className="font-bold">This model is NOT_TRAINED.</div>
                  <p className="mt-1">No trained weights are registered. It is never selected automatically and never generates detections or alerts. Train it on a genuine dataset and register the weights to make it available.</p>
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                    {[
                      ["Precision", evaluation?.test_metrics?.["metrics/precision(B)"]],
                      ["Recall", evaluation?.test_metrics?.["metrics/recall(B)"]],
                      ["mAP50", evaluation?.test_metrics?.["metrics/mAP50(B)"]],
                      ["mAP50-95", evaluation?.test_metrics?.["metrics/mAP50-95(B)"]]
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 p-2">
                        <div className="text-[9px] font-bold uppercase text-slate-500">{label}</div>
                        <div className="text-sm font-extrabold">{typeof value === "number" ? value.toFixed(3) : "-"}</div>
                      </div>
                    ))}
                  </div>
                  {perClass.length > 0 && (
                    <div className="overflow-x-auto">
                      <DataTable
                        rows={perClass}
                        rowKey={(row) => row.class_name}
                        columns={[
                          { key: "class_name", label: "Class" },
                          { key: "mAP50", label: "mAP50", render: (row) => Number(row.mAP50 || 0).toFixed(4) },
                          { key: "mAP50_95", label: "mAP50-95", render: (row) => Number(row.mAP50_95 || 0).toFixed(4) },
                          { key: "precision", label: "P", render: (row) => Number(row.precision || 0).toFixed(4) },
                          { key: "recall", label: "R", render: (row) => Number(row.recall || 0).toFixed(4) }
                        ]}
                      />
                    </div>
                  )}
                  {perClass.length === 0 && <div className="text-[10px] text-slate-400">No class-wise evaluation registered.</div>}
                </>
              )}
            </div>
          ) : <EmptyState message="Select a model to inspect its training and evaluation data." />}
        </Panel>
      </div>

      <Panel title="Registered datasets" description="Dataset provenance used by the training pipeline." className="mt-4">
        {datasets.length === 0 ? <EmptyState message="No datasets registered." /> : (
          <DataTable
            rows={datasets}
            rowKey={(row) => row.id}
            columns={[
              { key: "name", label: "Dataset" },
              { key: "source", label: "Source" },
              { key: "license", label: "License" },
              { key: "image_count", label: "Images" },
              { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> }
            ]}
          />
        )}
      </Panel>

      <Panel title="How model selection respects training status" description="Automatic scene-based selection only ever picks AVAILABLE models." className="mt-4">
        <div className="grid md:grid-cols-3 gap-3 text-[11px] text-slate-600">
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
            <CheckCircle2 className="w-4 h-4 text-emerald-500 mb-1" />
            <div className="font-bold text-emerald-800">AVAILABLE models run</div>
            <p className="mt-0.5">Trained, weights present and compatible with the camera input.</p>
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <AlertTriangle className="w-4 h-4 text-amber-500 mb-1" />
            <div className="font-bold text-amber-800">NOT_TRAINED models never run</div>
            <p className="mt-0.5">Untrained models are excluded from automatic and manual selection.</p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <Database className="w-4 h-4 text-slate-400 mb-1" />
            <div className="font-bold text-slate-700">No retraining required</div>
            <p className="mt-0.5">Existing trained models and checkpoints are preserved and reused as-is.</p>
          </div>
        </div>
      </Panel>
    </div>
  );
}