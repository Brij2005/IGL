import React, { useEffect, useState } from "react";
import { Cpu, RefreshCw } from "lucide-react";
import {
  fetchDatasetsApi,
  fetchModelEvaluationApi,
  fetchModelHealthApi,
  fetchModelVersionsApi,
  fetchModelsApi,
  reloadModelApi
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

export default function ModelsView() {
  const [models, setModels] = useState([]);
  const [health, setHealth] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [versions, setVersions] = useState([]);
  const [evaluation, setEvaluation] = useState(null);
  const [selectedModel, setSelectedModel] = useState("model-water-leakage");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    setError("");
    const [modelResult, healthResult, datasetResult, versionResult, evaluationResult] = await Promise.all([
      fetchModelsApi(),
      fetchModelHealthApi(),
      fetchDatasetsApi(),
      fetchModelVersionsApi(selectedModel),
      fetchModelEvaluationApi(selectedModel)
    ]);
    setModels(modelResult || []);
    setHealth(healthResult?.models || []);
    setDatasets(datasetResult || []);
    setVersions(versionResult || []);
    setEvaluation(evaluationResult || null);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel]);

  const reload = async (modelId) => {
    setBusy(modelId);
    const result = await reloadModelApi(modelId);
    if (!result || result.detail) setError(result?.detail || "Model reload failed");
    await load();
    setBusy("");
  };

  const healthById = Object.fromEntries(health.map((item) => [item.model_id, item]));
  const activeModel = models.find((model) => model.id === selectedModel);
  const perClass = evaluation?.per_class || [];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="AI Models & Datasets"
        description="Active model version, version history, class-wise test performance and dataset metadata. Metrics come from the backend registry and genuine evaluation runs."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}

      <div className="grid xl:grid-cols-2 gap-4">
        <Panel title="Model registry" description="Independent model configuration and version status.">
          {models.length === 0 ? <EmptyState message="No models registered." /> : (
            <DataTable
              rows={models}
              rowKey={(row) => row.id}
              columns={[
                { key: "name", label: "Model", render: (row) => <div><div className="font-semibold">{row.name}</div><div className="text-[10px] text-slate-500">{row.task_type}</div></div> },
                { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
                { key: "loaded", label: "Loaded", render: (row) => healthById[row.id]?.loaded ? "Yes" : "No" },
                { key: "classes", label: "Classes", render: (row) => {
                  const classes = row.classes || {};
                  const keys = Object.keys(classes);
                  return keys.length ? keys.slice(0, 8).map((key) => classes[key]).join(", ") : "Not configured";
                } },
                { key: "actions", label: "Actions", render: (row) => (
                  <button className={buttonSecondary} onClick={() => reload(row.id)} disabled={busy === row.id}>
                    <RefreshCw className="w-3.5 h-3.5 inline mr-1" />{busy === row.id ? "Reloading..." : "Reload"}
                  </button>
                ) }
              ]}
            />
          )}
        </Panel>

        <div className="space-y-4">
          <Panel
            title="Active leakage model"
            description="Active version and class-wise independent test performance."
            actions={<select className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs" value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>
              {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
            </select>}
          >
            {activeModel ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={activeModel.status} />
                  <span className="text-xs font-bold text-slate-700">Active version: {evaluation?.active_version || activeModel.active_version || "-"}</span>
                  <span className="text-[10px] text-slate-500">{evaluation?.weights_path || activeModel.weights_path}</span>
                </div>
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
                    <table className="min-w-full text-left text-[11px]">
                      <thead className="border-b border-slate-200 text-[9px] uppercase text-slate-500">
                        <tr><th className="px-2 py-1.5">Class</th><th className="px-2 py-1.5">mAP50</th><th className="px-2 py-1.5">mAP50-95</th><th className="px-2 py-1.5">P</th><th className="px-2 py-1.5">R</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {perClass.map((row) => (
                          <tr key={row.class_name}>
                            <td className="px-2 py-1.5 font-semibold">{row.class_name}</td>
                            <td className="px-2 py-1.5">{Number(row.mAP50 || 0).toFixed(4)}</td>
                            <td className="px-2 py-1.5">{Number(row.mAP50_95 || 0).toFixed(4)}</td>
                            <td className="px-2 py-1.5">{Number(row.precision || 0).toFixed(4)}</td>
                            <td className="px-2 py-1.5">{Number(row.recall || 0).toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {perClass.length === 0 && <div className="text-[10px] text-slate-400">No class-wise evaluation is registered for this model yet.</div>}
              </div>
            ) : <EmptyState message="Select a model to inspect version and evaluation data." />}
          </Panel>

          <Panel title="Version history" description="Preserved versions with weights and dataset provenance.">
            {versions.length === 0 ? <EmptyState message="No model versions recorded." /> : (
              <DataTable
                rows={versions}
                rowKey={(row) => row.id}
                columns={[
                  { key: "version", label: "Version" },
                  { key: "weights_path", label: "Weights" },
                  { key: "dataset_id", label: "Dataset" },
                  { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
                  { key: "created_at", label: "Created", render: (row) => new Date(row.created_at).toLocaleString() }
                ]}
              />
            )}
          </Panel>
        </div>
      </div>

      <div className="grid xl:grid-cols-2 gap-4 mt-4">
        <Panel title="Dataset metadata" description="Registered datasets used by the active model pipeline.">
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

        <Panel title="Model evaluation notes" description="Weak classes are shown explicitly, not hidden by aggregate metrics.">
          <div className="space-y-2 text-[11px] text-slate-600 leading-relaxed">
            <p><span className="font-bold">Active model:</span> {activeModel?.name || selectedModel} version {evaluation?.active_version || activeModel?.active_version || "-"}.</p>
            <p><span className="font-bold">Class-wise metrics</span> come from the independent test split. Classes with low mAP should not be treated as reliable production detectors.</p>
            <p><span className="font-bold">Rollback:</span> preserved versions are available in the version history. Do not delete v1 or v2 weights.</p>
            <p><span className="font-bold">Pipe burst:</span> not represented by a reliable training dataset and is not claimed by this model.</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}