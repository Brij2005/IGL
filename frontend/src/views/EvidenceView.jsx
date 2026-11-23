import React, { useEffect, useRef, useState } from "react";
import { Image, RefreshCw, Upload } from "lucide-react";
import { evidenceUrl, fetchEvidenceApi, uploadEvidenceApi } from "../services/api";
import {
  PageHeader,
  Panel,
  EmptyState,
  ErrorState,
  buttonPrimary,
  buttonSecondary
} from "../components/OperationsUI";

export default function EvidenceView() {
  const [evidence, setEvidence] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  const load = async () => {
    const result = await fetchEvidenceApi(200);
    setEvidence(result || []);
  };

  useEffect(() => {
    load();
  }, []);

  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    const result = await uploadEvidenceApi(file);
    if (!result || result.detail) setError(result?.detail || "Evidence upload failed");
    await load();
    setBusy(false);
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Evidence Gallery"
        description="Evidence is stored by the backend when a detection event is created. Access is authenticated. Do not present test or simulated frames as real operational footage."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <Panel
        title="Evidence uploads"
        description="JPG, PNG and WEBP files up to 25 MB."
        actions={<label className={buttonPrimary + " cursor-pointer"}><Upload className="w-3.5 h-3.5 inline mr-1" />Upload<input ref={inputRef} type="file" className="hidden" accept="image/jpeg,image/png,image/webp" onChange={upload} disabled={busy} /></label>}
      >
        {evidence.length === 0 ? <EmptyState message="No evidence files available." /> : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {evidence.map((item) => (
              <a key={item.id} href={evidenceUrl(item.id)} target="_blank" rel="noreferrer" className="group rounded-lg border border-slate-200 overflow-hidden bg-slate-50">
                <div className="aspect-video bg-slate-900 flex items-center justify-center overflow-hidden">
                  <img src={evidenceUrl(item.id)} alt={item.id} className="w-full h-full object-cover group-hover:scale-105 transition-transform" />
                </div>
                <div className="p-2">
                  <div className="text-[10px] font-bold text-slate-700 truncate">{item.id}</div>
                  <div className="text-[9px] text-slate-500">{item.camera_id || "unassigned"} · {new Date(item.created_at).toLocaleString()}</div>
                </div>
              </a>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}