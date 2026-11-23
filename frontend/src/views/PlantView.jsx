import React, { useEffect, useState } from "react";
import { Network, Plus, RefreshCw } from "lucide-react";
import { createPipelineApi, createPlantLocationApi, fetchPipelinesApi, fetchPlantLocationsApi } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  EmptyState,
  ErrorState,
  FormField,
  inputClass,
  buttonPrimary,
  buttonSecondary
} from "../components/OperationsUI";

export default function PlantView() {
  const [locations, setLocations] = useState([]);
  const [pipelines, setPipelines] = useState([]);
  const [locationForm, setLocationForm] = useState({ name: "", code: "", description: "" });
  const [pipelineForm, setPipelineForm] = useState({ name: "", plant_location_id: "", material: "", description: "" });
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    const [locationsResult, pipelinesResult] = await Promise.all([fetchPlantLocationsApi(), fetchPipelinesApi()]);
    setLocations(locationsResult || []);
    setPipelines(pipelinesResult || []);
    if (!pipelineForm.plant_location_id && locationsResult?.length) {
      setPipelineForm((current) => ({ ...current, plant_location_id: locationsResult[0].id }));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addLocation = async () => {
    if (!locationForm.name.trim()) return;
    const result = await createPlantLocationApi(locationForm);
    if (!result || result.detail) setError(result?.detail || "Plant location creation failed");
    setLocationForm({ name: "", code: "", description: "" });
    await load();
  };

  const addPipeline = async () => {
    if (!pipelineForm.name.trim() || !pipelineForm.plant_location_id) return;
    const result = await createPipelineApi(pipelineForm);
    if (!result || result.detail) setError(result?.detail || "Pipeline creation failed");
    setPipelineForm({ name: "", plant_location_id: pipelineForm.plant_location_id, material: "", description: "" });
    await load();
  };

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Plant & Pipeline Management"
        description="Map cameras and incidents to physical plant locations and pipelines. This is operational configuration data, not simulated dashboard content."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Plant locations" description="Registered plant areas.">
          {locations.length === 0 ? <EmptyState message="No plant locations registered." /> : (
            <DataTable
              rows={locations}
              rowKey={(row) => row.id}
              columns={[
                { key: "name", label: "Location" },
                { key: "code", label: "Code", render: (row) => row.code || "-" },
                { key: "description", label: "Description", render: (row) => row.description || "-" }
              ]}
            />
          )}
          <div className="mt-4 grid md:grid-cols-3 gap-2 items-end">
            <FormField label="Name"><input className={inputClass} value={locationForm.name} onChange={(event) => setLocationForm({ ...locationForm, name: event.target.value })} /></FormField>
            <FormField label="Code"><input className={inputClass} value={locationForm.code} onChange={(event) => setLocationForm({ ...locationForm, code: event.target.value })} /></FormField>
            <FormField label="Description"><input className={inputClass} value={locationForm.description} onChange={(event) => setLocationForm({ ...locationForm, description: event.target.value })} /></FormField>
          </div>
          <button className={buttonPrimary + " mt-2"} onClick={addLocation}><Plus className="w-3.5 h-3.5 inline mr-1" />Add location</button>
        </Panel>

        <Panel title="Pipelines" description="Pipeline assets used for leakage mapping.">
          {pipelines.length === 0 ? <EmptyState message="No pipelines registered." /> : (
            <DataTable
              rows={pipelines}
              rowKey={(row) => row.id}
              columns={[
                { key: "name", label: "Pipeline" },
                { key: "plant_location_id", label: "Location", render: (row) => row.plant_location_id || "-" },
                { key: "material", label: "Material", render: (row) => row.material || "-" }
              ]}
            />
          )}
          <div className="mt-4 grid md:grid-cols-2 gap-2 items-end">
            <FormField label="Name"><input className={inputClass} value={pipelineForm.name} onChange={(event) => setPipelineForm({ ...pipelineForm, name: event.target.value })} /></FormField>
            <FormField label="Plant location">
              <select className={inputClass} value={pipelineForm.plant_location_id} onChange={(event) => setPipelineForm({ ...pipelineForm, plant_location_id: event.target.value })}>
                <option value="">Select location</option>
                {locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}
              </select>
            </FormField>
            <FormField label="Material"><input className={inputClass} value={pipelineForm.material} onChange={(event) => setPipelineForm({ ...pipelineForm, material: event.target.value })} /></FormField>
            <FormField label="Description"><input className={inputClass} value={pipelineForm.description} onChange={(event) => setPipelineForm({ ...pipelineForm, description: event.target.value })} /></FormField>
          </div>
          <button className={buttonPrimary + " mt-2"} onClick={addPipeline}><Plus className="w-3.5 h-3.5 inline mr-1" />Add pipeline</button>
        </Panel>
      </div>
    </div>
  );
}