import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Boxes,
  Camera,
  Cpu,
  DoorOpen,
  LogIn,
  LogOut,
  Radio,
  RefreshCw,
  ShieldCheck,
  Thermometer,
  Upload,
  Video,
} from "lucide-react";
import {
  analyzeRecordedVideoApi,
  createAssetApi,
  createGatePassApi,
  fetchAccessRecordsApi,
  fetchAssetMovementsApi,
  fetchAssetsApi,
  fetchHardwareStatusApi,
  fetchInferenceWorkersApi,
  fetchSecurityDashboardApi,
  fetchSecurityEventsApi,
  fetchThermalReadingsApi,
  fetchVideoAnalysesApi,
  recordAssetMovementApi,
  reviewAccessRecordApi,
  verifyAssetApi
} from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonPrimary,
  buttonSecondary,
  buttonDanger,
} from "../components/OperationsUI";

const EMPTY_STATS = {
  entry_total: 0,
  exit_total: 0,
  pending_verification: 0,
  asset_movements: 0,
  unmatched_assets: 0,
  thermal_readings: 0,
  thermal_alerts: 0,
  video_analyses: 0,
  assets_total: 0,
  cctv_cameras: 0,
  thermal_cameras: 0,
  cameras_online: 0,
};

function SourceTag({ label, tone }) {
  const tones = {
    thermal: "bg-orange-50 text-orange-700 border-orange-200",
    cctv: "bg-sky-50 text-sky-700 border-sky-200",
    access: "bg-emerald-50 text-emerald-700 border-emerald-200",
    rfid: "bg-violet-50 text-violet-700 border-violet-200",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold ${tones[tone] || tones.cctv}`}>
      {label}
    </span>
  );
}

function StatCard({ icon: Icon, label, value, tone }) {
  const tones = {
    sky: "text-sky-600",
    orange: "text-orange-600",
    emerald: "text-emerald-600",
    amber: "text-amber-600",
    red: "text-red-600",
    violet: "text-violet-600",
  };
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${tones[tone] || tones.sky}`} />
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      </div>
      <div className="mt-1 text-xl font-extrabold text-slate-900">{value}</div>
    </div>
  );
}

export default function SecurityDashboardView() {
  const [stats, setStats] = useState(EMPTY_STATS);
  const [access, setAccess] = useState([]);
  const [movements, setMovements] = useState([]);
  const [assets, setAssets] = useState([]);
  const [thermal, setThermal] = useState([]);
  const [analyses, setAnalyses] = useState([]);
  const [hardware, setHardware] = useState([]);
  const [events, setEvents] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [assetForm, setAssetForm] = useState({ asset_name: "", asset_category: "GENERAL", rfid_tag: "", department: "" });
  const [creatingAsset, setCreatingAsset] = useState(false);
  const [verifyForm, setVerifyForm] = useState({ rfid: "", barcode: "", asset_id: "" });
  const [verifyResult, setVerifyResult] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const [gatePassForm, setGatePassForm] = useState({ asset_id: "", employee_id: "", purpose: "" });
  const fileRef = useRef(null);

  const load = async () => {
    setError("");
    const [dash, acc, mov, ast, thm, ana, hw, evt, wrk] = await Promise.all([
      fetchSecurityDashboardApi(),
      fetchAccessRecordsApi({ limit: 50 }),
      fetchAssetMovementsApi(50),
      fetchAssetsApi(),
      fetchThermalReadingsApi(50),
      fetchVideoAnalysesApi(20),
      fetchHardwareStatusApi(),
      fetchSecurityEventsApi(50),
      fetchInferenceWorkersApi(),
    ]);
    setStats({ ...EMPTY_STATS, ...(dash || {}) });
    setAccess(acc || []);
    setMovements(mov || []);
    setAssets(ast || []);
    setThermal(thm || []);
    setAnalyses(ana || []);
    setHardware(hw?.devices || []);
    setEvents(evt || []);
    setWorkers(wrk || []);
  };

  useEffect(() => {
    load();
  }, []);

  const review = async (record, decision) => {
    setBusy(`${record.id}:${decision}`);
    const result = await reviewAccessRecordApi(record.id, decision, "");
    if (result?.detail) setError(result.detail);
    await load();
    setBusy("");
  };

  const handleAnalyze = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setAnalyzing(true);
    setError("");
    const result = await analyzeRecordedVideoApi(file, { sourceKind: "cctv", maxFrames: 300 });
    setAnalyzing(false);
    if (result?.detail) {
      setError(Array.isArray(result.detail) ? JSON.stringify(result.detail) : result.detail);
    } else {
      await load();
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const createAsset = async (event) => {
    event.preventDefault();
    if (!assetForm.asset_name.trim()) return;
    setCreatingAsset(true);
    setError("");
    const result = await createAssetApi(assetForm);
    setCreatingAsset(false);
    if (result?.detail) {
      setError(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
      return;
    }
    setAssetForm({ asset_name: "", asset_category: "GENERAL", rfid_tag: "", department: "" });
    await load();
  };

  // Asset verification against the register (RFID / barcode / asset id).
  const verifyAsset = async (event) => {
    event.preventDefault();
    setError("");
    setVerifying(true);
    const result = await verifyAssetApi({ rfid: verifyForm.rfid, barcode: verifyForm.barcode, asset_id: verifyForm.asset_id });
    setVerifying(false);
    if (result?.detail) {
      setError(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
      return;
    }
    setVerifyResult(result);
  };

  // Record an asset movement; verification status comes from the backend
  // (gate pass + register), never from the AI detection.
  const recordMovement = async (assetId, direction) => {
    setError("");
    const result = await recordAssetMovementApi(assetId, { direction, verification_source: "RFID" });
    if (result?.detail) {
      setError(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
      return;
    }
    setVerifyResult(null);
    await load();
  };

  // Create a gate pass for an asset.
  const issueGatePass = async (event) => {
    event.preventDefault();
    setError("");
    const result = await createGatePassApi({
      asset_id: gatePassForm.asset_id,
      issued_to_employee_id: gatePassForm.employee_id,
      purpose: gatePassForm.purpose
    });
    if (result?.detail) {
      setError(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
      return;
    }
    setGatePassForm({ asset_id: "", employee_id: "", purpose: "" });
    await load();
  };

  const accessColumns = [
    { key: "direction", label: "Direction", render: (row) => (
      <span className={`inline-flex items-center gap-1 font-bold ${row.direction === "ENTRY" ? "text-emerald-700" : "text-sky-700"}`}>
        {row.direction === "ENTRY" ? <LogIn className="w-3.5 h-3.5" /> : <LogOut className="w-3.5 h-3.5" />}
        {row.direction}
      </span>
    ) },
    { key: "person_name", label: "Person", render: (row) => row.person_name || row.person_id || "-" },
    { key: "gate_id", label: "Gate", render: (row) => row.gate_id || "-" },
    { key: "verification_status", label: "Verification", render: (row) => <StatusBadge status={row.verification_status} /> },
    { key: "verification_source", label: "Source", render: (row) => (
      row.verification_source === "RFID" ? <SourceTag label="RFID" tone="rfid" />
        : row.verification_source === "ACCESS_CONTROL" ? <SourceTag label="Access Control" tone="access" />
          : <SourceTag label="AI / Unverified" tone="cctv" />
    ) },
    { key: "recorded_at", label: "Recorded", render: (row) => new Date(row.recorded_at).toLocaleString() },
    { key: "actions", label: "Review", render: (row) => (
      row.verification_status === "UNVERIFIED" ? (
        <div className="flex gap-1.5">
          <button className={buttonSecondary} onClick={() => review(row, "VERIFIED")} disabled={busy === `${row.id}:VERIFIED`}>
            <ShieldCheck className="w-3.5 h-3.5 inline mr-1" />Verify
          </button>
          <button className={buttonDanger} onClick={() => review(row, "DISMISSED")} disabled={busy === `${row.id}:DISMISSED`}>Dismiss</button>
        </div>
      ) : <span className="text-[10px] text-slate-400">Reviewed</span>
    ) }
  ];

  const movementColumns = [
    { key: "asset_id", label: "Asset" },
    { key: "direction", label: "Direction", render: (row) => row.direction },
    { key: "verification_status", label: "Verification", render: (row) => <StatusBadge status={row.verification_status} /> },
    { key: "gate_id", label: "Gate", render: (row) => row.gate_id || "-" },
    { key: "moved_at", label: "Moved", render: (row) => new Date(row.moved_at).toLocaleString() },
  ];

  const thermalColumns = [
    { key: "camera_id", label: "Camera", render: (row) => row.camera_id || "-" },
    { key: "temperature_c", label: "Temp (C)", render: (row) => row.temperature_c != null ? `${row.temperature_c} C` : "Not calibrated" },
    { key: "threshold_c", label: "Threshold", render: (row) => row.threshold_c != null ? `${row.threshold_c} C` : "-" },
    { key: "threshold_exceeded", label: "Alert", render: (row) => row.threshold_exceeded ? <StatusBadge status="ALERT" /> : <span className="text-[10px] text-slate-400">Normal</span> },
    { key: "calibrated", label: "Calibrated", render: (row) => row.calibrated ? <SourceTag label="Calibrated" tone="thermal" /> : <span className="text-[10px] text-slate-400">No</span> },
  ];

  const analysisColumns = [
    { key: "source_kind", label: "Source", render: (row) => row.source_kind === "thermal" ? <SourceTag label="Thermal AI" tone="thermal" /> : <SourceTag label="Normal CCTV AI" tone="cctv" /> },
    { key: "filename", label: "File", render: (row) => row.filename || "-" },
    { key: "model_id", label: "Model" },
    { key: "frames_processed", label: "Frames" },
    { key: "people_max", label: "Max People" },
    { key: "entry_count", label: "Entry" },
    { key: "exit_count", label: "Exit" },
    { key: "created_at", label: "Analyzed", render: (row) => new Date(row.created_at).toLocaleString() },
  ];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Smart Entry & Exit Security"
        description="AI-assisted entry/exit monitoring using normal CCTV and thermal footage, with access-control and RFID verification. All uncertain detections require human review. AI never decides asset ownership or identifies anyone as a violator."
        actions={
          <>
            <button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>
            <label className={`${buttonPrimary} cursor-pointer`}>
              <Upload className="w-3.5 h-3.5 inline mr-1" />Analyze Recorded Video
              <input ref={fileRef} type="file" accept="video/*" className="hidden" onChange={handleAnalyze} disabled={analyzing} />
            </label>
          </>
        }
      />

      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {analyzing && <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-xs text-sky-700">RECORDED VIDEO ANALYSIS in progress. Frames are decoded and analyzed by the backend. This is not a live camera feed.</div>}

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2 mb-4">
        <StatCard icon={LogIn} label="Entry records" value={stats.entry_total} tone="emerald" />
        <StatCard icon={LogOut} label="Exit records" value={stats.exit_total} tone="sky" />
        <StatCard icon={ShieldCheck} label="Pending verification" value={stats.pending_verification} tone="amber" />
        <StatCard icon={Boxes} label="Asset movements" value={stats.asset_movements} tone="violet" />
        <StatCard icon={AlertTriangle} label="Unmatched assets" value={stats.unmatched_assets} tone="red" />
        <StatCard icon={Thermometer} label="Thermal readings" value={stats.thermal_readings} tone="orange" />
        <StatCard icon={Camera} label="Thermal cams" value={stats.thermal_cameras} tone="orange" />
        <StatCard icon={Video} label="CCTV cams" value={stats.cctv_cameras} tone="sky" />
      </div>

      <div className="grid lg:grid-cols-[1.6fr_1fr] gap-4 mb-4">
        <Panel
          title="Entry & Exit monitoring"
          description="Entry and exit records with access-control / RFID verification. Unverified events are sent for human review."
        >
          {access.length === 0 ? <EmptyState message="No entry/exit records yet. Analyze a recorded video or register access events to populate this table." /> : <DataTable columns={accessColumns} rows={access} rowKey={(row) => row.id} />}
        </Panel>

        <Panel title="Hardware adapters" description="Modular interfaces for future devices. No device is reported as connected unless configured.">
          {hardware.length === 0 ? <EmptyState message="No hardware adapters configured." /> : (
            <div className="space-y-2">
              {hardware.map((device) => (
                <div key={device.kind} className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Radio className="w-4 h-4 text-slate-500" />
                    <div>
                      <div className="text-xs font-bold text-slate-800">{device.kind.replace(/_/g, " ")}</div>
                      <div className="text-[10px] text-slate-400">{device.device_type}</div>
                    </div>
                  </div>
                  <StatusBadge status={device.connected ? "ONLINE" : "NOT_CONNECTED"} />
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel
        className="mb-4"
        title="Live camera inference workers"
        description="Real background workers that analyze running camera frames and produce events. A camera only appears here when it is actually connected and being analyzed."
      >
        {workers.length === 0 ? (
          <EmptyState message="No cameras are being analyzed. Start a camera from Camera Management to enable live AI inference." />
        ) : (
          <div className="space-y-2">
            {workers.map((worker) => (
              <div key={worker.camera_id} className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-sky-600" />
                  <div>
                    <div className="text-xs font-bold text-slate-800">{worker.camera_id}</div>
                    <div className="text-[10px] text-slate-400">
                      Models: {(worker.model_ids || []).join(", ") || "-"} · Frames analyzed: {worker.frames_processed}
                    </div>
                    {worker.last_error && <div className="text-[10px] text-red-500">Last error: {worker.last_error}</div>}
                  </div>
                </div>
                <StatusBadge status={worker.running ? "ONLINE" : "STOPPED"} />
              </div>
            ))}
          </div>
        )}
      </Panel>

      <div className="grid lg:grid-cols-2 gap-4 mb-4">
        <Panel title="Asset movement records" description="Asset movements verified against the asset register and gate passes.">
          {movements.length === 0 ? <EmptyState message="No asset movements recorded." /> : <DataTable columns={movementColumns} rows={movements} rowKey={(row) => row.id} />}
        </Panel>

        <Panel title="Thermal AI" description="Temperature is only shown when a calibrated thermal source provides it. Not a medical diagnosis.">
          {thermal.length === 0 ? <EmptyState message="No thermal readings. Connect a calibrated thermal camera or record calibrated temperature data." /> : <DataTable columns={thermalColumns} rows={thermal} rowKey={(row) => row.id} />}
        </Panel>
      </div>

      <div className="grid lg:grid-cols-2 gap-4 mb-4">
        <Panel title="Recorded video analyses" description="RECORDED VIDEO ANALYSIS only. Never a live feed.">
          {analyses.length === 0 ? <EmptyState message="No recorded videos analyzed yet. Use 'Analyze Recorded Video' to upload footage." /> : <DataTable columns={analysisColumns} rows={analyses} rowKey={(row) => row.id} />}
        </Panel>

        <Panel title="Security events" description="Unverified entries, unmatched asset movements and configured thermal threshold alerts. All require human review.">
          {events.length === 0 ? <EmptyState message="No security events. Empty state shown when there is no data." /> : (
            <div className="space-y-2">
              {events.map((event) => (
                <div key={`${event.type}-${event.ref}`} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-800">{event.type.replace(/_/g, " ")}</span>
                    <StatusBadge status={event.severity} />
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">
                    Ref {event.ref} · {event.gate_id || event.camera_id || "-"} · {new Date(event.recorded_at).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Company asset register" description="AI visual detection alone never decides whether an object belongs to the company. Ownership is resolved from these records.">
        <div className="grid lg:grid-cols-[1.4fr_1fr] gap-4">
          <div>
            {assets.length === 0 ? <EmptyState message="No assets registered. Add company assets below." /> : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-xs">
                  <thead className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-bold">Asset</th>
                      <th className="px-3 py-2 font-bold">Category</th>
                      <th className="px-3 py-2 font-bold">RFID</th>
                      <th className="px-3 py-2 font-bold">Status</th>
                      <th className="px-3 py-2 font-bold">Movement</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {assets.map((asset) => (
                      <tr key={asset.id} className="hover:bg-slate-50/70">
                        <td className="px-3 py-2.5 text-slate-700">{asset.asset_name}</td>
                        <td className="px-3 py-2.5 text-slate-600">{asset.asset_category}</td>
                        <td className="px-3 py-2.5 text-slate-600">{asset.rfid_tag || "-"}</td>
                        <td className="px-3 py-2.5"><StatusBadge status={asset.status} /></td>
                        <td className="px-3 py-2.5">
                          <div className="flex gap-1.5">
                            <button className={buttonSecondary} onClick={() => recordMovement(asset.id, "EXIT")}>Out</button>
                            <button className={buttonSecondary} onClick={() => recordMovement(asset.id, "ENTRY")}>In</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <form className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2" onSubmit={verifyAsset}>
              <div className="text-xs font-bold text-slate-700">Verify asset from register (RFID / barcode / asset ID)</div>
              <div className="grid grid-cols-3 gap-2">
                <input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
                  placeholder="RFID tag" value={verifyForm.rfid}
                  onChange={(e) => setVerifyForm({ ...verifyForm, rfid: e.target.value })} />
                <input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
                  placeholder="Barcode" value={verifyForm.barcode}
                  onChange={(e) => setVerifyForm({ ...verifyForm, barcode: e.target.value })} />
                <input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
                  placeholder="Asset ID" value={verifyForm.asset_id}
                  onChange={(e) => setVerifyForm({ ...verifyForm, asset_id: e.target.value })} />
              </div>
              <button type="submit" className={buttonPrimary} disabled={verifying}>
                <Boxes className="w-3.5 h-3.5 inline mr-1" />{verifying ? "Verifying..." : "Verify"}
              </button>
              {verifyResult && (
                <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[11px]">
                  {verifyResult.verified ? (
                    <span className="text-emerald-700 font-bold">
                      Matched in asset register: {verifyResult.asset?.asset_name || verifyResult.asset?.id}
                    </span>
                  ) : (
                    <span className="text-red-600 font-bold">
                      No matching asset record ({verifyResult.reason || "NO_MATCHING_ASSET_RECORD"})
                    </span>
                  )}
                </div>
              )}
            </form>

            <form className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2" onSubmit={issueGatePass}>
              <div className="text-xs font-bold text-slate-700">Issue gate pass</div>
              <div className="grid grid-cols-3 gap-2">
                <input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
                  placeholder="Asset ID" value={gatePassForm.asset_id}
                  onChange={(e) => setGatePassForm({ ...gatePassForm, asset_id: e.target.value })} />
                <input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
                  placeholder="Employee ID" value={gatePassForm.employee_id}
                  onChange={(e) => setGatePassForm({ ...gatePassForm, employee_id: e.target.value })} />
                <input className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
                  placeholder="Purpose" value={gatePassForm.purpose}
                  onChange={(e) => setGatePassForm({ ...gatePassForm, purpose: e.target.value })} />
              </div>
              <button type="submit" className={buttonPrimary}>
                <ShieldCheck className="w-3.5 h-3.5 inline mr-1" />Issue pass
              </button>
            </form>
          </div>
          <form className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2" onSubmit={createAsset}>
            <div className="text-xs font-bold text-slate-700">Register new asset</div>
            <input
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
              placeholder="Asset name"
              value={assetForm.asset_name}
              onChange={(e) => setAssetForm({ ...assetForm, asset_name: e.target.value })}
            />
            <input
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
              placeholder="Category (e.g. TOOL, EQUIPMENT)"
              value={assetForm.asset_category}
              onChange={(e) => setAssetForm({ ...assetForm, asset_category: e.target.value })}
            />
            <input
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
              placeholder="RFID tag / barcode"
              value={assetForm.rfid_tag}
              onChange={(e) => setAssetForm({ ...assetForm, rfid_tag: e.target.value })}
            />
            <input
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none focus:border-sky-500"
              placeholder="Department"
              value={assetForm.department}
              onChange={(e) => setAssetForm({ ...assetForm, department: e.target.value })}
            />
            <button type="submit" className={buttonPrimary} disabled={creatingAsset}>
              <Boxes className="w-3.5 h-3.5 inline mr-1" />{creatingAsset ? "Saving..." : "Register asset"}
            </button>
          </form>
        </div>
      </Panel>
    </div>
  );
}