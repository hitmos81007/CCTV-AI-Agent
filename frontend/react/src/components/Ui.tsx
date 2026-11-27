import React, { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FilePlus2,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  UserCircle2,
  UserX,
  X,
} from "lucide-react";
import type { VerificationStatus } from "../types";
import { formatDistrictName } from "../lib/districts";

const STATUS_META: Record<
  VerificationStatus | string,
  { label: string; bg: string; text: string; border: string; icon: React.ReactNode }
> = {
  draft: {
    label: "Draft",
    bg: "bg-slate-800/80",
    text: "text-slate-200",
    border: "border-slate-600",
    icon: <Clock className="h-3.5 w-3.5" aria-hidden="true" />,
  },
  pending_review: {
    label: "Pending Review",
    bg: "bg-amber-950/60",
    text: "text-[#F7BF62]",
    border: "border-amber-500/40",
    icon: <Clock className="h-3.5 w-3.5" aria-hidden="true" />,
  },
  additional_info_required: {
    label: "Info Required",
    bg: "bg-amber-950/60",
    text: "text-[#F7BF62]",
    border: "border-amber-500/40",
    icon: <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />,
  },
  approved: {
    label: "Approved",
    bg: "bg-emerald-950/60",
    text: "text-[#5EE0AD]",
    border: "border-emerald-500/40",
    icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />,
  },
  rejected: {
    label: "Rejected",
    bg: "bg-rose-950/60",
    text: "text-[#FF9CA9]",
    border: "border-rose-500/40",
    icon: <UserX className="h-3.5 w-3.5" aria-hidden="true" />,
  },
  suspended: {
    label: "Suspended",
    bg: "bg-rose-950/60",
    text: "text-[#FF9CA9]",
    border: "border-rose-500/40",
    icon: <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />,
  },
};

export function StatusChip({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? {
    label: status.replace(/_/g, " "),
    bg: "bg-slate-800",
    text: "text-slate-200",
    border: "border-slate-600",
    icon: <Clock className="h-3.5 w-3.5" aria-hidden="true" />,
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${meta.bg} ${meta.text} ${meta.border}`}
    >
      {meta.icon}
      <span>{meta.label}</span>
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 border-b border-[#33465A]/70 pb-5 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-[#F1F5F9] sm:text-[28px]">
          {title}
        </h1>
        {subtitle && <p className="mt-1 text-sm text-[#A8B6C9]">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-[#33465A] bg-[#142133] p-5">
      <div className="text-xs font-medium uppercase tracking-wider text-[#A8B6C9]">{label}</div>
      <div className="tabular-nums mt-2 text-3xl font-semibold text-[#5EE0CA]">{value}</div>
      {hint && <div className="mt-1 text-xs text-[#A8B6C9]">{hint}</div>}
    </div>
  );
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="mb-5 flex flex-col gap-3 rounded-xl border border-rose-500/40 bg-rose-950/40 p-4 text-sm text-[#FF9CA9] sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{message}</span>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-lg border border-rose-400/50 bg-rose-900/40 px-3 py-1.5 text-xs font-medium text-rose-100 hover:bg-rose-900/70"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-[#33465A] bg-[#142133]/60 px-6 py-12 text-center">
      <h2 className="text-base font-semibold text-[#F1F5F9]">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-[#A8B6C9]">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function LoadingScreen({ label = "Restoring secure session..." }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-screen flex-col items-center justify-center bg-[#0B111C] px-4 text-center"
    >
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-[#33465A] border-t-[#5EE0CA]" />
      <p className="mt-4 text-sm text-[#A8B6C9]">{label}</p>
    </div>
  );
}

export function ConfirmModal({
  open,
  title,
  description,
  confirmLabel,
  requireReason,
  reasonLabel = "Reason",
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  requireReason?: boolean;
  reasonLabel?: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      setReason("");
      setError("");
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (requireReason && !reason.trim()) {
      setError("Please provide a clear administrative reason before continuing.");
      return;
    }
    onConfirm(reason.trim());
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
    >
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-xl border border-[#33465A] bg-[#142133] p-6 shadow-2xl"
      >
        <h2 id="confirm-modal-title" className="text-lg font-semibold text-[#F1F5F9]">
          {title}
        </h2>
        <p className="mt-2 text-sm text-[#A8B6C9]">{description}</p>
        <div className="mt-4">
          <label className="block text-xs font-medium text-[#A8B6C9]">
            {reasonLabel} {requireReason ? "(required)" : "(optional)"}
            <textarea
              rows={3}
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                setError("");
              }}
              placeholder="Enter administrative rationale..."
              className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
            />
          </label>
          {error && <p className="mt-1.5 text-xs text-[#FF9CA9]">{error}</p>}
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-lg border border-[#33465A] bg-transparent px-4 py-2 text-sm font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-[#5EE0CA] px-4 py-2 text-sm font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
          >
            {busy ? "Processing..." : confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}

interface NavItem {
  to: string;
  label: string;
  icon: React.ReactNode;
}

function BaseShell({
  brandSubtitle,
  navItems,
  email,
  badge,
  onSignOut,
  children,
}: {
  brandSubtitle: string;
  navItems: NavItem[];
  email: string | null;
  badge?: React.ReactNode;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname, location.search]);

  const sidebarContent = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 px-2 pb-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 text-[#5EE0CA]">
          <ShieldCheck className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <div className="text-sm font-semibold text-[#F1F5F9]">CCTV AI Agent</div>
          <div className="text-xs text-[#A8B6C9]">{brandSubtitle}</div>
        </div>
      </div>

      <nav className="flex-1 space-y-1" aria-label="Primary navigation">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-[#1B2A3D] text-[#5EE0CA]"
                  : "text-[#A8B6C9] hover:bg-[#1B2A3D]/60 hover:text-[#F1F5F9]"
              }`
            }
          >
            {item.icon}
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="mt-6 border-t border-[#33465A] pt-4">
        {badge && <div className="mb-3">{badge}</div>}
        <div className="truncate px-1 text-xs text-[#A8B6C9]" title={email ?? ""}>
          {email ?? "Authenticated"}
        </div>
        <button
          type="button"
          onClick={onSignOut}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-[#33465A] bg-[#142133] px-3 py-2 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
        >
          <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Sign Out</span>
        </button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-[#0B111C] text-[#F1F5F9] lg:flex">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r border-[#33465A] bg-[#101A29] p-5 lg:block">
        {sidebarContent}
      </aside>

      {/* Mobile header */}
      <header className="flex items-center justify-between border-b border-[#33465A] bg-[#101A29] px-4 py-3 lg:hidden">
        <Link to="/" className="flex items-center gap-2.5 text-sm font-semibold text-[#F1F5F9]">
          <ShieldCheck className="h-5 w-5 text-[#5EE0CA]" aria-hidden="true" />
          <span>CCTV AI Agent</span>
        </Link>
        <button
          type="button"
          aria-label={mobileOpen ? "Close navigation menu" : "Open navigation menu"}
          onClick={() => setMobileOpen((v) => !v)}
          className="rounded-lg border border-[#33465A] p-2 text-[#F1F5F9]"
        >
          {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="border-b border-[#33465A] bg-[#101A29] p-5 lg:hidden">
          {sidebarContent}
        </div>
      )}

      <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[1440px]">{children}</div>
      </main>
    </div>
  );
}

export function OfficerShell({
  email,
  onSignOut,
  children,
}: {
  email: string | null;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  return (
    <BaseShell
      brandSubtitle="Officer Workspace"
      email={email}
      onSignOut={onSignOut}
      badge={<StatusChip status="approved" />}
      navItems={[
        { to: "/dashboard", label: "Overview", icon: <LayoutDashboard className="h-4 w-4" /> },
        { to: "/investigations/new", label: "New Investigation", icon: <FilePlus2 className="h-4 w-4" /> },
        { to: "/history", label: "Investigation History", icon: <History className="h-4 w-4" /> },
        { to: "/account", label: "Account Information", icon: <UserCircle2 className="h-4 w-4" /> },
      ]}
    >
      {children}
    </BaseShell>
  );
}

export function AdminShell({
  districtId,
  email,
  onSignOut,
  children,
}: {
  districtId: string;
  email: string | null;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  return (
    <BaseShell
      brandSubtitle={`District Admin • ${formatDistrictName(districtId)}`}
      email={email}
      onSignOut={onSignOut}
      badge={
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 px-2.5 py-0.5 text-xs font-medium text-[#5EE0CA]">
          <UserCheck className="h-3.5 w-3.5" aria-hidden="true" />
          <span>District Administrator</span>
        </span>
      }
      navItems={[
        { to: "/admin", label: "Overview", icon: <LayoutDashboard className="h-4 w-4" /> },
        { to: "/admin?tab=pending_review", label: "Pending Applications", icon: <Clock className="h-4 w-4" /> },
        { to: "/admin?tab=approved", label: "Approved Officers", icon: <CheckCircle2 className="h-4 w-4" /> },
        { to: "/admin?tab=rejected", label: "Rejected Applications", icon: <UserX className="h-4 w-4" /> },
        { to: "/admin?tab=suspended", label: "Suspended Officers", icon: <ShieldAlert className="h-4 w-4" /> },
        { to: "/admin?tab=history", label: "Administrative History", icon: <History className="h-4 w-4" /> },
        { to: "/account", label: "Account Information", icon: <UserCircle2 className="h-4 w-4" /> },
      ]}
    >
      {children}
    </BaseShell>
  );
}
