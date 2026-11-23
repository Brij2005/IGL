import React, { useState } from "react";
import { LogIn, ShieldCheck } from "lucide-react";
import { loginApi } from "../services/api";

export default function LoginView({ onSuccess }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (!username.trim() || !password) {
      setError("Username and password are required.");
      return;
    }
    setBusy(true);
    try {
      const result = await loginApi(username.trim(), password);
      setPassword("");
      onSuccess?.(result);
    } catch (err) {
      setError(err?.message || "Login failed. Check your credentials.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-lg">
        <div className="flex items-center gap-2 mb-1">
          <ShieldCheck className="w-6 h-6 text-emerald-600" />
          <h1 className="text-lg font-extrabold text-slate-900">IGL VisionGuard</h1>
        </div>
        <p className="text-xs text-slate-500 mb-5">
          Authorized access only. Sign in with your assigned account.
        </p>

        {error && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
            {error}
          </div>
        )}

        <form className="space-y-3" onSubmit={submit}>
          <label className="block text-xs">
            <span className="font-semibold text-slate-600">Username</span>
            <input
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100"
              value={username}
              autoComplete="username"
              autoFocus
              onChange={(event) => setUsername(event.target.value)}
              placeholder="Enter your username"
            />
          </label>
          <label className="block text-xs">
            <span className="font-semibold text-slate-600">Password</span>
            <input
              type="password"
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100"
              value={password}
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Enter your password"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-slate-900 px-3 py-2.5 text-sm font-bold text-white hover:bg-slate-700 disabled:opacity-60"
          >
            <LogIn className="w-4 h-4 inline mr-1" />
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="mt-4 text-[10px] text-slate-400">
          Access is role-based (Admin / Operator / Viewer). Unauthenticated API requests are rejected.
        </p>
      </div>
    </div>
  );
}