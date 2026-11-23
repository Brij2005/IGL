import React, { useEffect, useState } from "react";
import { Bell, RefreshCw, Send } from "lucide-react";
import { fetchNotificationDeliveriesApi, fetchNotificationStatusApi, testNotificationsApi } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  StatusBadge,
  EmptyState,
  ErrorState,
  buttonPrimary,
  buttonSecondary
} from "../components/OperationsUI";

export default function NotificationsView() {
  const [status, setStatus] = useState({});
  const [deliveries, setDeliveries] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    const [statusResult, deliveriesResult] = await Promise.all([fetchNotificationStatusApi(), fetchNotificationDeliveriesApi(100)]);
    setStatus(statusResult || {});
    setDeliveries(deliveriesResult || []);
  };

  useEffect(() => {
    load();
  }, []);

  const test = async (channel) => {
    setBusy(channel);
    setError("");
    const result = await testNotificationsApi([channel]);
    if (!result || result.detail) setError(result?.detail || "Notification test failed");
    await load();
    setBusy("");
  };

  const channels = [
    ["IN_APP", "In-app"],
    ["EMAIL", "Email"],
    ["SMS", "SMS"],
    ["TELEGRAM", "Telegram"],
    ["WHATSAPP", "WhatsApp"]
  ];

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Notifications"
        description="Delivery status is only reported as successful when the provider confirms acceptance. Unconfigured providers remain explicitly not configured."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <div className="grid sm:grid-cols-2 xl:grid-cols-5 gap-3 mb-4">
        {channels.map(([channel, label]) => (
          <div key={channel} className="rounded-xl border border-slate-200 bg-white p-3">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-700"><Bell className="w-4 h-4 text-slate-400" />{label}</div>
            <div className="mt-2"><StatusBadge status={status[channel.toLowerCase()]?.configured ? "CONFIGURED" : "NOT_CONFIGURED"} /></div>
            <button className={buttonSecondary + " mt-3 w-full"} onClick={() => test(channel)} disabled={busy === channel}>
              <Send className="w-3.5 h-3.5 inline mr-1" />{busy === channel ? "Testing..." : "Test"}
            </button>
          </div>
        ))}
      </div>

      <Panel title="Delivery history" description="Provider attempts, statuses and errors recorded by the backend.">
        {deliveries.length === 0 ? <EmptyState message="No notification deliveries recorded." /> : (
          <DataTable
            rows={deliveries}
            rowKey={(row) => row.id}
            columns={[
              { key: "channel", label: "Channel" },
              { key: "recipient", label: "Recipient" },
              { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
              { key: "attempts", label: "Attempts" },
              { key: "provider_message_id", label: "Provider ID", render: (row) => row.provider_message_id || "-" },
              { key: "error_message", label: "Error", render: (row) => row.error_message || "-" },
              { key: "updated_at", label: "Updated", render: (row) => row.updated_at ? new Date(row.updated_at).toLocaleString() : "-" }
            ]}
          />
        )}
      </Panel>
    </div>
  );
}