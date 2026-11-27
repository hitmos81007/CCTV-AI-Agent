import React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchCases } from "../api";
import { EmptyState, ErrorBanner, MetricCard, PageHeader, StatusChip } from "../components/Ui";

export function Dashboard() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["cases"],
    queryFn: fetchCases,
  });

  const cases = data ?? [];
  const activeCount = cases.filter((c) => ["planning", "queued", "running"].includes(c.status)).length;
  const pendingFindings = cases.reduce((acc, c) => acc + (c.pending_finding_count || 0), 0);

  return (
    <div>
      <PageHeader
        title="Investigation Overview"
        subtitle="Authorized officer workspace • Real-time backend metrics"
        action={
          <Link
            to="/investigations/new"
            className="inline-flex rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
          >
            New Investigation
          </Link>
        }
      />

      {error && (
        <ErrorBanner
          message={(error as Error).message}
          onRetry={() => refetch()}
        />
      )}

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <MetricCard
          label="Total Investigations"
          value={error ? "Unavailable" : isLoading ? "—" : cases.length}
        />
        <MetricCard
          label="Active Investigations"
          value={error ? "Unavailable" : isLoading ? "—" : activeCount}
        />
        <MetricCard
          label="Pending Findings"
          value={error ? "Unavailable" : isLoading ? "—" : pendingFindings}
        />
      </div>

      {!error && !isLoading && cases.length === 0 ? (
        <EmptyState
          title="No investigations yet"
          description="Your officer verification is approved. Start a new investigation when you are ready to upload authorized footage."
          action={
            <Link
              to="/investigations/new"
              className="inline-flex rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
            >
              Create First Investigation
            </Link>
          }
        />
      ) : (
        <div className="rounded-xl border border-[#33465A] bg-[#142133] p-5">
          <h2 className="mb-3 text-sm font-semibold text-[#F1F5F9]">Recent Investigations</h2>
          <div className="divide-y divide-[#33465A]">
            {cases.slice(0, 8).map((c) => (
              <Link
                key={c.case_id}
                to={`/cases/${encodeURIComponent(c.case_id)}`}
                className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm hover:bg-[#1B2A3D]/40"
              >
                <div className="min-w-0">
                  <div className="font-medium text-[#F1F5F9]">{c.title || c.case_id}</div>
                  <div className="truncate text-xs text-[#A8B6C9]">{c.description}</div>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <StatusChip status={c.status} />
                  <span className="tabular-nums text-[#A8B6C9]">{c.video_count} videos</span>
                  <span className="tabular-nums text-[#A8B6C9]">{c.finding_count} findings</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
