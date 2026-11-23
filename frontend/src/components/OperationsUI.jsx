import React from "react";

export function PageHeader({ title, description, actions }) {
  return (
    <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 mb-5">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight text-slate-900">{title}</h1>
        <p className="text-xs text-slate-500 mt-1 max-w-3xl">{description}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Panel({ title, description, actions, children, className = "" }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-bold text-slate-800">{title}</h2>}
            {description && <p className="text-[11px] text-slate-500 mt-0.5">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function EmptyState({ message = "No records available yet." }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center">
      <p className="text-sm font-semibold text-slate-600">{message}</p>
      <p className="text-[11px] text-slate-400 mt-1">Data will appear here after the backend records it.</p>
    </div>
  );
}

export function LoadingState({ message = "Loading..." }) {
  return <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-center text-xs text-slate-500 animate-pulse">{message}</div>;
}

export function ErrorState({ message }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
      <div className="font-bold">Backend request failed</div>
      <div className="mt-1 break-words">{message || "The backend is unreachable or returned an error."}</div>
    </div>
  );
}

export function StatusBadge({ status }) {
  const normalized = String(status || "UNKNOWN").toUpperCase();
  const palette = normalized.includes("ONLINE") || normalized.includes("CONFIRMED") || normalized.includes("AVAILABLE")
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : normalized.includes("OFFLINE") || normalized.includes("FAILED") || normalized.includes("DISMISSED")
      ? "bg-red-50 text-red-700 border-red-200"
      : normalized.includes("POSSIBLE") || normalized.includes("SUSPECTED") || normalized.includes("RECONNECTING")
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : "bg-slate-50 text-slate-600 border-slate-200";
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold ${palette}`}>{normalized.replace(/_/g, " ")}</span>;
}

export function DataTable({ columns, rows, emptyMessage, rowKey }) {
  if (!rows || rows.length === 0) return <EmptyState message={emptyMessage} />;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-xs">
        <thead className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-500">
          <tr>
            {columns.map((column) => (
              <th key={column.key} className="px-3 py-2 font-bold whitespace-nowrap">{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row, index) => (
            <tr key={rowKey ? rowKey(row, index) : index} className="hover:bg-slate-50/70">
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-2.5 align-top text-slate-700">
                  {column.render ? column.render(row) : row[column.key] ?? "-"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FormField({ label, children, hint }) {
  return (
    <label className="block text-xs">
      <span className="font-semibold text-slate-600">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-[10px] text-slate-400">{hint}</span>}
    </label>
  );
}

export const inputClass = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-800 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100";
export const buttonPrimary = "rounded-lg bg-slate-900 px-3 py-2 text-xs font-bold text-white hover:bg-slate-700 disabled:opacity-60";
export const buttonSecondary = "rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-60";
export const buttonDanger = "rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700 hover:bg-red-100";