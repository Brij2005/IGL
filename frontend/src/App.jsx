import React, { useEffect, useState } from "react";
import { SafetyProvider, useSafety } from "./context/SafetyContext";
import Navbar from "./components/Navbar";
import Sidebar from "./components/Sidebar";
import RealInputBanner from "./components/RealInputBanner";
import EvidenceModal from "./components/EvidenceModal";
import LoginView from "./views/LoginView";
import { fetchSessionApi, isAuthenticated, logoutApi } from "./services/api";

import DashboardView from "./views/DashboardView";
import CameraInputView from "./views/CameraInputView";
import LiveMonitoringView from "./views/LiveMonitoringView";
import ExpectedPersonnelView from "./views/ExpectedPersonnelView";
import PackagingInspectionView from "./views/PackagingInspectionView";
import AlertsView from "./views/AlertsView";
import PersonnelAnomalyView from "./views/PersonnelAnomalyView";
import PersonnelMonitoringView from "./views/PersonnelMonitoringView";
import PipeInspectionView from "./views/PipeInspectionView";
import NearMissView from "./views/NearMissView";
import CameraHealthView from "./views/CameraHealthView";
import AnalyticsView from "./views/AnalyticsView";
import ConfigurationView from "./views/ConfigurationView";
import CameraManagementView from "./views/CameraManagementView";
import CameraSetupView from "./views/CameraSetupView";
import LeakageView from "./views/LeakageView";
import EvidenceView from "./views/EvidenceView";
import PlantView from "./views/PlantView";
import ModelsView from "./views/ModelsView";
import ReportsView from "./views/ReportsView";
import NotificationsView from "./views/NotificationsView";
import SystemHealthView from "./views/SystemHealthView";
import AuditLogView from "./views/AuditLogView";
import SettingsView from "./views/SettingsView";
import UsersView from "./views/UsersView";
import SecurityDashboardView from "./views/SecurityDashboardView";

function MainContent() {
  const { activeTab } = useSafety();

  return (
    <main className="flex-1 overflow-y-auto bg-slate-50 pb-12">
      {activeTab === "dashboard" && <DashboardView />}
      {activeTab === "camera-input" && <CameraInputView />}
      {activeTab === "live-monitoring" && <LiveMonitoringView />}
      {activeTab === "expected-personnel" && <ExpectedPersonnelView />}
      {activeTab === "personnel-monitoring" && <PersonnelMonitoringView />}
      {(activeTab === "pipe-inspection" || activeTab === "image-camera-inspection") && <PipeInspectionView />}
      {activeTab === "packaging" && <PackagingInspectionView />}
      {activeTab === "alerts" && <AlertsView />}
      {activeTab === "personnel" && <PersonnelAnomalyView />}
      {activeTab === "near-miss" && <NearMissView />}
      {activeTab === "camera-health" && <CameraHealthView />}
      {activeTab === "analytics" && <AnalyticsView />}
      {activeTab === "config" && <ConfigurationView />}
      {activeTab === "camera-management" && <CameraManagementView />}
      {activeTab === "camera-setup" && <CameraSetupView />}
      {activeTab === "leakage" && <LeakageView />}
      {activeTab === "evidence" && <EvidenceView />}
      {activeTab === "plant" && <PlantView />}
      {activeTab === "models" && <ModelsView />}
      {activeTab === "reports" && <ReportsView />}
      {activeTab === "notifications" && <NotificationsView />}
      {activeTab === "system-health" && <SystemHealthView />}
      {activeTab === "audit-logs" && <AuditLogView />}
      {activeTab === "settings" && <SettingsView />}
      {activeTab === "users" && <UsersView />}
      {activeTab === "security" && <SecurityDashboardView />}
    </main>
  );
}

export default function App() {
  const [authState, setAuthState] = useState("checking"); // checking | required | open
  const [session, setSession] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Probe the backend: when auth is enabled the session endpoint is
      // authoritative. When auth is disabled (local development) the backend
      // returns the anonymous admin user, so the app opens directly.
      const user = await fetchSessionApi();
      if (cancelled) return;
      if (user) {
        setSession(user);
        setAuthState("open");
      } else if (isAuthenticated()) {
        setAuthState("required"); // token rejected -> force re-login
      } else {
        const probe = await fetch(`${(import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000/api").replace(/\/+$/, "")}/auth/me`);
        if (cancelled) return;
        if (probe.status === 401) setAuthState("required");
        else if (probe.ok) {
          const data = await probe.json().catch(() => null);
          setSession(data?.user || null);
          setAuthState("open");
        } else setAuthState("required");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onExpired = () => setAuthState("required");
    window.addEventListener("igl-auth-expired", onExpired);
    return () => window.removeEventListener("igl-auth-expired", onExpired);
  }, []);

  const handleLogout = async () => {
    await logoutApi();
    setSession(null);
    setAuthState("required");
  };

  if (authState === "checking") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-100 text-sm text-slate-500">
        <div className="w-full text-center">Checking session...</div>
      </div>
    );
  }

  if (authState === "required") {
    return (
      <LoginView
        onSuccess={async (result) => {
          setSession(result?.user || null);
          setAuthState("open");
        }}
      />
    );
  }

  return (
    <SafetyProvider>
      <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col font-sans">
        <Navbar />
        <RealInputBanner />
        <div className="flex-1 flex overflow-hidden">
          <Sidebar />
          <MainContent />
        </div>
        <EvidenceModal />
        {session && (
          <div className="fixed bottom-3 right-3 z-50 flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[10px] shadow-md">
            <span className="font-semibold text-slate-700">{session.username}</span>
            <span className="rounded-full bg-slate-100 px-1.5 py-0.5 font-bold uppercase text-slate-500">{session.role}</span>
            <button onClick={handleLogout} className="font-bold text-red-600 hover:underline">
              Sign out
            </button>
          </div>
        )}
      </div>
    </SafetyProvider>
  );
}