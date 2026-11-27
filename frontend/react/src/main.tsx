import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { AdminShell, ErrorBanner, LoadingScreen, OfficerShell } from "./components/Ui";
import { Account } from "./pages/Account";
import { AdminDashboard } from "./pages/AdminDashboard";
import { AuthCallback } from "./pages/AuthCallback";
import { Create } from "./pages/Create";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Login } from "./pages/Login";
import { Onboarding } from "./pages/Onboarding";
import { Register } from "./pages/Register";
import { ResetPassword } from "./pages/ResetPassword";
import { VerificationStatus } from "./pages/VerificationStatus";
import { Workspace } from "./pages/Workspace";
import "./style.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
  },
});

function RoleLandingRedirect() {
  const { session, authLoading, me, meLoading, meError, refreshMe, signOut } = useAuth();

  if (authLoading || (session && meLoading)) {
    return <LoadingScreen />;
  }
  if (!session) {
    return <Navigate to="/login" replace />;
  }
  if (meError) {
    return (
      <div className="mx-auto max-w-lg px-4 py-16">
        <ErrorBanner message={meError.message} onRetry={refreshMe} />
        <button
          type="button"
          onClick={signOut}
          className="rounded-lg border border-[#33465A] bg-[#142133] px-4 py-2 text-xs text-[#F1F5F9]"
        >
          Sign Out
        </button>
      </div>
    );
  }
  if (me?.is_district_admin) {
    return <Navigate to="/admin" replace />;
  }
  if (me?.is_approved_officer) {
    return <Navigate to="/dashboard" replace />;
  }
  if (!me?.profile || me.profile.verification_status === "draft") {
    return <Navigate to="/onboarding" replace />;
  }
  return <Navigate to="/verification-status" replace />;
}

function RequireAuthenticated({ children }: { children: React.ReactNode }) {
  const { session, authLoading } = useAuth();
  const location = useLocation();
  if (authLoading) return <LoadingScreen />;
  if (!session) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <>{children}</>;
}

function RequireOnboardingApplicant({ children }: { children: React.ReactNode }) {
  const { session, authLoading, me, meLoading } = useAuth();
  const location = useLocation();
  if (authLoading || (session && meLoading)) return <LoadingScreen />;
  if (!session) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (me?.is_district_admin) {
    return <Navigate to="/admin" replace />;
  }
  if (me?.is_approved_officer) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}

function RequireApprovedOfficer({ children }: { children: React.ReactNode }) {
  const { session, authLoading, me, meLoading, signOut } = useAuth();
  if (authLoading || (session && meLoading)) return <LoadingScreen />;
  if (!session) return <Navigate to="/login" replace />;
  if (!me?.is_approved_officer) {
    return <Navigate to={me?.is_district_admin ? "/admin" : "/verification-status"} replace />;
  }
  return (
    <OfficerShell email={me.email} onSignOut={signOut}>
      {children}
    </OfficerShell>
  );
}

function RequireDistrictAdmin({ children }: { children: React.ReactNode }) {
  const { session, authLoading, me, meLoading, signOut } = useAuth();
  if (authLoading || (session && meLoading)) return <LoadingScreen />;
  if (!session) return <Navigate to="/login" replace />;
  if (!me?.is_district_admin || !me.admin_district_id) {
    return <Navigate to="/" replace />;
  }
  return (
    <AdminShell districtId={me.admin_district_id} email={me.email} onSignOut={signOut}>
      {children}
    </AdminShell>
  );
}

function AccountRoute() {
  const { session, authLoading, me, meLoading, signOut } = useAuth();
  if (authLoading || (session && meLoading)) return <LoadingScreen />;
  if (!session) return <Navigate to="/login" replace />;
  if (me?.is_district_admin && me.admin_district_id) {
    return (
      <AdminShell districtId={me.admin_district_id} email={me.email} onSignOut={signOut}>
        <Account />
      </AdminShell>
    );
  }
  if (me?.is_approved_officer) {
    return (
      <OfficerShell email={me.email} onSignOut={signOut}>
        <Account />
      </OfficerShell>
    );
  }
  return (
    <div className="min-h-screen bg-[#0B111C] px-4 py-10 text-[#F1F5F9]">
      <div className="mx-auto max-w-2xl">
        <Account />
      </div>
    </div>
  );
}

function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[#0B111C] px-4 text-center text-[#F1F5F9]">
      <h1 className="text-2xl font-semibold">Page Not Found</h1>
      <p className="mt-2 text-sm text-[#A8B6C9]">The requested route does not exist.</p>
      <Link
        to="/"
        className="mt-5 rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D]"
      >
        Return Home
      </Link>
    </div>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<RoleLandingRedirect />} />
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/auth/callback" element={<AuthCallback />} />
            <Route path="/reset-password" element={<ResetPassword />} />

            <Route
              path="/onboarding"
              element={
                <RequireOnboardingApplicant>
                  <Onboarding />
                </RequireOnboardingApplicant>
              }
            />
            <Route
              path="/verification-status"
              element={
                <RequireOnboardingApplicant>
                  <VerificationStatus />
                </RequireOnboardingApplicant>
              }
            />

            <Route
              path="/dashboard"
              element={
                <RequireApprovedOfficer>
                  <Dashboard />
                </RequireApprovedOfficer>
              }
            />
            <Route
              path="/investigations/new"
              element={
                <RequireApprovedOfficer>
                  <Create />
                </RequireApprovedOfficer>
              }
            />
            <Route path="/create" element={<Navigate to="/investigations/new" replace />} />
            <Route
              path="/history"
              element={
                <RequireApprovedOfficer>
                  <History />
                </RequireApprovedOfficer>
              }
            />
            <Route
              path="/cases/:id"
              element={
                <RequireApprovedOfficer>
                  <Workspace />
                </RequireApprovedOfficer>
              }
            />

            <Route
              path="/admin"
              element={
                <RequireDistrictAdmin>
                  <AdminDashboard />
                </RequireDistrictAdmin>
              }
            />

            <Route path="/account" element={<AccountRoute />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
