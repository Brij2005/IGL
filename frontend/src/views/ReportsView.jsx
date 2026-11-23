import React, { useEffect, useState } from "react";
import { Download, FileBarChart, RefreshCw } from "lucide-react";
import { fetchReportsApi, generateReportApi, reportDownloadUrl } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  EmptyState,
  ErrorState,
  buttonPrimary,
  buttonSecondary
} from "../components/OperationsUI";

export default function ReportsView() {
  const [reports, setReports] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    const result = await fetchReportsApi(100);
    setReports(result || []);
  };

  useEffect(() => {
    load();
  }, []);

  const generate = async (reportType) => {
    setBusy(reportType);
    setError("");
    const result = await generateReportApi(reportType, "json");
    if (!result || result.detail) setError(result?.detail || "Report generation failed");
    await load();
    setBusy("");
  };

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Reports & Analytics"
        description="Reports are generated from stored backend records. No dashboard counts or report figures are hardcoded."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <Panel
        title="Generate report"
        description="Daily, weekly, monthly and incident-focused reports use actual stored data."
        className="mb-4"
      >
        <div className="flex flex-wrap gap-2">
          {[
            ["daily", "Daily"],
            ["weekly", "Weekly"],
            ["monthly", "Monthly"],
            ["leakage", "Leakage incidents"],
            ["safety", "Safety incidents"],
            ["camera_uptime", "Camera uptime"],
            ["alert_response", "Alert response"],
            ["model_performance", "Model performance"]
          ].map(([type, label]) => (
            <button key={type} className={buttonPrimary} onClick={() => generate(type)} disabled={busy === type}>
              <FileBarChart className="w-3.5 h-3.5 inline mr-1" />{busy === type ? "Generating..." : label}
            </button>
          ))}
        </div>
      </Panel>

      <Panel title="Report history" description="Generated reports can be downloaded from the backend.">
        {reports.length === 0 ? <EmptyState message="No reports generated yet." /> : (
          <DataTable
            rows={reports}
            rowKey={(row) => row.id}
            columns={[
              { key: "id", label: "Report" },
              { key: "report_type", label: "Type" },
              { key: "period_start", label: "Period start", render: (row) => new Date(row.period_start).toLocaleString() },
              { key: "period_end", label: "Period end", render: (row) => new Date(row.period_end).toLocaleString() },
              { key: "created_at", label: "Generated", render: (row) => new Date(row.created_at).toLocaleString() },
              { key: "download", label: "Download", render: (row) => (
                <a className={buttonSecondary} href={reportDownloadUrl(row.id)} target="_blank" rel="noreferrer"><Download className="w-3.5 h-3.5 inline mr-1" />JSON</a>
              ) }
            ]}
          />
        )}
      </Panel>
    </div>
  );
}