import React, { useEffect, useState } from "react";
import { RefreshCw, Trash2, UserPlus } from "lucide-react";
import { createUserApi, deleteUserApi, fetchUsersApi } from "../services/api";
import {
  PageHeader,
  Panel,
  DataTable,
  EmptyState,
  ErrorState,
  FormField,
  inputClass,
  buttonPrimary,
  buttonSecondary,
  buttonDanger
} from "../components/OperationsUI";

export default function UsersView() {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({ username: "", email: "", password: "", role: "viewer" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    setError("");
    setUsers((await fetchUsersApi()) || []);
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    setError("");
    if (form.username.trim().length < 3 || form.password.length < 8) {
      setError("Username must be at least 3 characters and password at least 8 characters.");
      return;
    }
    setBusy("create");
    const result = await createUserApi(form);
    if (!result || result.detail) setError(result?.detail || "User creation failed");
    else {
      setForm({ username: "", email: "", password: "", role: "viewer" });
      await load();
    }
    setBusy("");
  };

  const remove = async (userId) => {
    if (!window.confirm("Delete this user?")) return;
    setBusy(userId);
    const result = await deleteUserApi(userId);
    if (!result || result.detail) setError(result?.detail || "User deletion failed");
    await load();
    setBusy("");
  };

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="User & Role Management"
        description="Administrator-managed accounts. Passwords are hashed by the backend. Role-based permissions are enforced by API dependencies."
        actions={<button className={buttonSecondary} onClick={load}><RefreshCw className="w-3.5 h-3.5 inline mr-1" />Refresh</button>}
      />
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      <div className="grid lg:grid-cols-[1.5fr_1fr] gap-4">
        <Panel title="Users" description="Accounts currently registered in the backend.">
          {users.length === 0 ? <EmptyState message="No users found." /> : (
            <DataTable
              rows={users}
              rowKey={(row) => row.id}
              columns={[
                { key: "username", label: "Username" },
                { key: "email", label: "Email", render: (row) => row.email || "-" },
                { key: "role", label: "Role" },
                { key: "is_active", label: "Active", render: (row) => row.is_active ? "Yes" : "No" },
                { key: "last_login_at", label: "Last login", render: (row) => row.last_login_at ? new Date(row.last_login_at).toLocaleString() : "-" },
                { key: "actions", label: "Actions", render: (row) => (
                  <button className={buttonDanger} onClick={() => remove(row.id)} disabled={busy === row.id}>
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                ) }
              ]}
            />
          )}
        </Panel>
        <Panel title="Create user" description="New accounts are created with the selected role.">
          <div className="space-y-3">
            <FormField label="Username"><input className={inputClass} value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></FormField>
            <FormField label="Email"><input className={inputClass} type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></FormField>
            <FormField label="Password"><input className={inputClass} type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /></FormField>
            <FormField label="Role">
              <select className={inputClass} value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
                <option value="viewer">Viewer</option>
                <option value="operator">Operator</option>
                <option value="admin">Administrator</option>
              </select>
            </FormField>
            <button className={buttonPrimary} onClick={create} disabled={busy === "create"}>
              <UserPlus className="w-3.5 h-3.5 inline mr-1" />{busy === "create" ? "Creating..." : "Create user"}
            </button>
          </div>
        </Panel>
      </div>
    </div>
  );
}