import React, { useEffect, useRef, useState } from "react";
import { Map, RefreshCw, Save, Trash2 } from "lucide-react";
import {
  cameraFrameUrl,
  createZoneApi,
  deleteZoneApi,
  fetchCameras,
  fetchModelsApi,
  fetchZonesApi,
  updateCameraConfigApi
} from "../services/api";
import {
  PageHeader,
  Panel,
  EmptyState,
  ErrorState,
  FormField,
  inputClass,
  buttonPrimary,
  buttonSecondary,
  buttonDanger,
  StatusBadge
} from "../components/OperationsUI";

export default function CameraSetupView() {
  const [cameras, setCameras] = useState([]);
  const [models, setModels] = useState([]);
  const [zones, setZones] = useState([]);
  const [selectedCamera, setSelectedCamera] = useState("");
  const [points, setPoints] = useState([]);
  const [zoneName, setZoneName] = useState("Pipeline Monitoring Zone");
  const [zoneType, setZoneType] = useState("MONITORING");
  const [modelIds, setModelIds] = useState(["model-water-leakage"]);
  const [config, setConfig] = useState({ min_confidence: 0.35, min_frames: 3, cooldown_seconds: 180 });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [previewKey, setPreviewKey] = useState(0);
  const canvasRef = useRef(null);

  const load = async () => {
    setError("");
    const [cams, zonesResult, modelResult] = await Promise.all([fetchCameras(), fetchZonesApi(), fetchModelsApi()]);
    setCameras(cams || []);
    setModels(modelResult || []);
    if (!selectedCamera && cams?.length) {
      setSelectedCamera(cams[0].id);
      setZones(await fetchZonesApi(cams[0].id));
    } else if (selectedCamera) {
      setZones(await fetchZonesApi(selectedCamera));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedCamera) return;
    fetchZonesApi(selectedCamera).then(setZones);
    setPoints([]);
    setPreviewKey((value) => value + 1);
  }, [selectedCamera]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (points.length === 0) return;
    context.lineWidth = 3;
    context.strokeStyle = "#0ea5e9";
    context.fillStyle = "rgba(14, 165, 233, 0.18)";
    context.beginPath();
    points.forEach((point, index) => {
      const x = point[0] * canvas.width;
      const y = point[1] * canvas.height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    if (points.length > 2) context.closePath();
    context.fill();
    context.stroke();
    context.fillStyle = "#0284c7";
    points.forEach((point) => {
      context.beginPath();
      context.arc(point[0] * canvas.width, point[1] * canvas.height, 5, 0, Math.PI * 2);
      context.fill();
    });
  }, [points]);

  const addPoint = (event) => {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    setPoints((current) => [...current, [Number(x.toFixed(4)), Number(y.toFixed(4))]]);
  };

  const saveZone = async () => {
    if (!selectedCamera) return;
    if (points.length < 3) {
      setError("Draw at least three points for a monitoring polygon.");
      return;
    }
    setError("");
    setBusy("zone");
    const result = await createZoneApi({
      camera_id: selectedCamera,
      zone_name: zoneName,
      zone_type: zoneType,
      polygon_coords: points,
      model_ids: modelIds
    });
    if (!result || result.detail) setError(result?.detail || "Zone save failed");
    else {
      setPoints([]);
      setZones(await fetchZonesApi(selectedCamera));
    }
    setBusy("");
  };

  const saveConfig = async () => {
    if (!selectedCamera) return;
    setError("");
    setBusy("config");
    const result = await updateCameraConfigApi(selectedCamera, {
      config: {
        model_ids: modelIds,
        thresholds: {
          min_confidence: Number(config.min_confidence),
          min_frames: Number(config.min_frames),
          cooldown_seconds: Number(config.cooldown_seconds)
        }
      }
    });
    if (!result || result.detail) setError(result?.detail || "Configuration save failed");
    setBusy("");
  };

  const removeZone = async (zoneId) => {
    await deleteZoneApi(zoneId);
    setZones(await fetchZonesApi(selectedCamera));
  };

  const selected = cameras.find((camera) => camera.id === selectedCamera);

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Camera Angle & Monitoring Zones"
        description="Adjust the physical camera angle at installation, then capture a frame and draw the pipeline region. Coordinates are stored relative to the current frame and can be redrawn after any camera adjustment."
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {cameras.length === 0 ? <EmptyState message="Register a camera first in Camera Management." /> : (
        <div className="grid xl:grid-cols-[1.4fr_1fr] gap-4">
          <div className="space-y-4">
            <Panel
              title="Live frame preview"
              description="If the camera is offline, start it from Camera Management. The preview is a backend-decoded MJPEG frame, not a simulated feed."
              actions={<button className={buttonSecondary} onClick={() => setPreviewKey((value) => value + 1)}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
            >
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <select className={inputClass + " max-w-xs"} value={selectedCamera} onChange={(event) => setSelectedCamera(event.target.value)}>
                  {cameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.name} — {camera.source_type}</option>)}
                </select>
                {selected && <StatusBadge status={selected.status} />}
              </div>
              <div className="relative overflow-hidden rounded-lg border border-slate-300 bg-slate-950" style={{ aspectRatio: "16 / 9" }}>
                {selected?.status === "ONLINE" ? (
                  <img key={previewKey} src={cameraFrameUrl(selectedCamera)} alt="Camera preview" className="absolute inset-0 w-full h-full object-contain" />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">Camera is not streaming. Start it or check its connection.</div>
                )}
                <canvas
                  ref={canvasRef}
                  width={640}
                  height={360}
                  onClick={addPoint}
                  className="absolute inset-0 w-full h-full cursor-crosshair"
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className={buttonSecondary} onClick={() => setPoints([])}>Clear points</button>
                <button className={buttonSecondary} onClick={() => setPoints((current) => current.slice(0, -1))}>Undo point</button>
                <span className="text-[11px] text-slate-500 self-center">{points.length} polygon points</span>
              </div>
            </Panel>

            <Panel title="Draw monitoring region" description="Click the preview to add polygon vertices. Save the zone after drawing.">
              <div className="grid md:grid-cols-3 gap-3">
                <FormField label="Zone name">
                  <input className={inputClass} value={zoneName} onChange={(event) => setZoneName(event.target.value)} />
                </FormField>
                <FormField label="Zone type">
                  <select className={inputClass} value={zoneType} onChange={(event) => setZoneType(event.target.value)}>
                    <option value="MONITORING">Monitoring</option>
                    <option value="RESTRICTED">Restricted</option>
                    <option value="PIPELINE">Pipeline</option>
                    <option value="TRIPWIRE">Tripwire</option>
                  </select>
                </FormField>
                <FormField label="Assigned models">
                  <select multiple className={inputClass} value={modelIds} onChange={(event) => setModelIds(Array.from(event.target.selectedOptions).map((option) => option.value))}>
                    {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
                  </select>
                </FormField>
              </div>
              <button className={buttonPrimary + " mt-3"} onClick={saveZone} disabled={busy === "zone"}><Save className="w-3.5 h-3.5 inline mr-1" />Save zone</button>
            </Panel>
          </div>

          <div className="space-y-4">
            <Panel title="Saved zones" description="Existing polygons for this camera.">
              {zones.length === 0 ? <EmptyState message="No zones saved for this camera." /> : (
                <div className="space-y-2">
                  {zones.map((zone) => (
                    <div key={zone.id} className="rounded-lg border border-slate-200 p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="text-xs font-bold text-slate-800">{zone.zone_name}</div>
                          <div className="text-[10px] text-slate-500 mt-0.5">{zone.zone_type} · {zone.polygon_coords?.length || 0} points</div>
                        </div>
                        <button className={buttonDanger} onClick={() => removeZone(zone.id)}><Trash2 className="w-3.5 h-3.5" /></button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            <Panel title="Detection thresholds" description="Applied by the event engine for this camera.">
              <div className="grid grid-cols-2 gap-3">
                <FormField label="Min confidence">
                  <input className={inputClass} type="number" min="0" max="1" step="0.05" value={config.min_confidence} onChange={(event) => setConfig({ ...config, min_confidence: event.target.value })} />
                </FormField>
                <FormField label="Min frames">
                  <input className={inputClass} type="number" min="1" max="60" value={config.min_frames} onChange={(event) => setConfig({ ...config, min_frames: event.target.value })} />
                </FormField>
                <FormField label="Cooldown (seconds)">
                  <input className={inputClass} type="number" min="10" max="3600" value={config.cooldown_seconds} onChange={(event) => setConfig({ ...config, cooldown_seconds: event.target.value })} />
                </FormField>
                <FormField label="Assigned models">
                  <select multiple className={inputClass} value={modelIds} onChange={(event) => setModelIds(Array.from(event.target.selectedOptions).map((option) => option.value))}>
                    {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
                  </select>
                </FormField>
              </div>
              <button className={buttonPrimary + " mt-3"} onClick={saveConfig} disabled={busy === "config"}>Save camera configuration</button>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}