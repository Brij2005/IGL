import React, { useEffect, useState } from "react";
import { RefreshCw, Save } from "lucide-react";
import { fetchSettingsApi, updateSettingsApi } from "../services/api";
import {
  PageHeader,
  Panel,
  EmptyState,
  ErrorState,
  FormField,
  inputClass,
  buttonPrimary,
  buttonSecondary
} from "../components/OperationsUI";

export default function SettingsView() {
  const [settings, setSettings] = useState({});
  const [form, setForm] = useState({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = async () => {
    const result = await fetchSettingsApi();
    setSettings(result || {});
    setForm(result || {});
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setError("");
    setMessage("");
    const result = await updateSettingsApi(form);
    if (!result || result.detail) setError(result?.detail || "Settings update failed");
    else setMessage("Settings saved to the backend.");
  };

  const keys = Object.keys(settings);

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="General Settings"
        description="Non-secret operational settings are stored in the backend. Secrets and provider credentials are read from environment variables and are never exposed here."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {message && <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800">{message}</div>}
      <Panel title="Operational settings" description="Edit values and save.">
        {keys.length === 0 ? <EmptyState message="No operational settings have been saved yet." /> : (
          <div className="grid md:grid-cols-2 gap-3">
            {keys.map((key) => (
              <FormField key={key} label={key}>
                {typeof settings[key] === "object" ? (
                  <textarea
                    className={inputClass + " min-h-[90px] font-mono"}
                    value={JSON.stringify(form[key], null, 2)}
                    onChange={(event) => {
                      try { setForm({ ...form, [key]: JSON.parse(event.target.value) }); }
                      catch { setForm({ ...form, [key]: event.target.value }); }
                    }}
                  />
                ) : (
                  <input className={inputClass} value={form[key] ?? ""} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
                )}
              </FormField>
            ))}
          </div>
        )}
        <button className={buttonPrimary + " mt-3"} onClick={save}><Save className="w-3.5 h-3.5 inline mr-1" />Save settings</button>
      </Panel>
      <Panel title="Environment-managed secrets" description="Do not put these in the frontend or in app_settings." className="mt-4">
        <div className="text-[11px] text-slate-600 leading-relaxed">
          <p><span className="font-mono">SECRET_KEY</span>, <span className="font-mono">ADMIN_PASSWORD</span>, <span className="font-mono">WHATSAPP_ACCESS_TOKEN</span>, <span className="font-mono">TELEGRAM_BOT_TOKEN</span>, <span className="font-mono">SMTP_PASSWORD</span> and camera credentials must be supplied by the deployment environment.</p>
          <p className="mt-2">Camera credentials are encrypted in SQLite with a key derived from <span className="font-mono">SECRET_KEY</span> and are never returned to the frontend.</p>
        </div>
      </Panel>
    </div>
  );
}