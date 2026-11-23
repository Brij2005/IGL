import React, { useEffect, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { fetchAuditLogsApi } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  EmptyState,
  ErrorState,
  buttonSecondary
} from "../components/OperationsUI";

export default function AuditLogView() {
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState("");

  const load = async () => {
    const result = await fetchAuditLogsApi(200);
    setLogs(result || []);
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Audit Logs"
        description="Security-relevant actions are recorded by the backend: login-adjacent user changes, camera configuration, zone changes, incident reviews, reports and evidence uploads."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <Panel title="Audit history" description="Latest 200 recorded actions.">
        {logs.length === 0 ? <EmptyState message="No audit records yet." /> : (
          <DataTable
            rows={logs}
            rowKey={(row) => row.id}
            columns={[
              { key: "created_at", label: "Time", render: (row) => new Date(row.created_at).toLocaleString() },
              { key: "actor", label: "Actor" },
              { key: "action", label: "Action" },
              { key: "resource_type", label: "Resource", render: (row) => row.resource_type || "-" },
              { key: "resource_id", label: "Resource ID", render: (row) => row.resource_id || "-" },
              { key: "ip_address", label: "IP", render: (row) => row.ip_address || "-" }
            ]}
          />
        )}
      </Panel>
    </div>
  );
}