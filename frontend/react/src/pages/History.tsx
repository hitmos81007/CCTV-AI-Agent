import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchCases } from "../api";
import { EmptyState, ErrorBanner, PageHeader, StatusChip } from "../components/Ui";

export function History() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "draft" | "running" | "completed">("all");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["cases"],
    queryFn: fetchCases,
  });

  const allCases = data ?? [];
  const filtered = allCases.filter((c) => {
    const matchesSearch =
      !search.trim() ||
      c.case_id.toLowerCase().includes(search.toLowerCase()) ||
      (c.title || "").toLowerCase().includes(search.toLowerCase()) ||
      c.description.toLowerCase().includes(search.toLowerCase());

    if (!matchesSearch) return false;

    if (statusFilter === "draft") return c.status === "draft";
    if (statusFilter === "running") return ["planning", "queued", "running"].includes(c.status);
    if (statusFilter === "completed") return ["completed", "partially_completed"].includes(c.status);
    return true;
  });

  return (
    <div>
      <PageHeader
        title="Investigation History"
        subtitle="Cases owned by your verified officer account • Persistent candidate findings & review records"
      />
      {error && <ErrorBanner message={(error as Error).message} onRetry={() => refetch()} />}

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter your investigations by title or description..."
          className="w-full max-w-md rounded-lg border border-[#33465A] bg-[#142133] px-3 py-2 text-sm text-[#F1F5F9] focus:border-[#5EE0CA] focus:outline-none"
        />

        <div className="flex rounded-lg border border-[#33465A] bg-[#142133] p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setStatusFilter("all")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              statusFilter === "all" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            All ({allCases.length})
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("running")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              statusFilter === "running" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            Active ({allCases.filter((c) => ["planning", "queued", "running"].includes(c.status)).length})
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("completed")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              statusFilter === "completed" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            Completed ({allCases.filter((c) => ["completed", "partially_completed"].includes(c.status)).length})
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter("draft")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              statusFilter === "draft" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            Drafts ({allCases.filter((c) => c.status === "draft").length})
          </button>
        </div>
      </div>

      {!isLoading && !error && filtered.length === 0 ? (
        <EmptyState
          title="No matching investigations"
          description="No investigations match your active filters or search terms."
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((c) => (
            <Link
              key={c.case_id}
              to={`/cases/${encodeURIComponent(c.case_id)}`}
              className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-[#33465A] bg-[#142133] p-4 transition-all hover:border-[#5EE0CA]/60 hover:bg-[#1B2A3D]/60"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-[#F1F5F9] text-sm">{c.title || c.case_id}</span>
                  <StatusChip status={c.status} />
                </div>
                <div className="mt-1 truncate text-xs text-[#A8B6C9]">{c.description}</div>
              </div>

              <div className="flex flex-wrap items-center gap-3 text-xs">
                <div className="rounded-lg bg-[#0B111C] px-2.5 py-1 text-[#A8B6C9] border border-[#33465A]">
                  <span className="font-semibold text-[#F1F5F9]">{c.video_count}</span> clips
                </div>
                <div className="rounded-lg bg-[#0B111C] px-2.5 py-1 text-[#A8B6C9] border border-[#33465A]">
                  <span className="font-semibold text-[#5EE0CA]">{c.finding_count}</span> candidates
                </div>
                {c.pending_finding_count > 0 && (
                  <div className="rounded-lg bg-amber-950/40 px-2.5 py-1 text-amber-300 border border-amber-500/30">
                    <span className="font-semibold">{c.pending_finding_count}</span> pending review
                  </div>
                )}
                <span className="text-[11px] text-[#94A3B8]">
                  {new Date(c.created_at).toLocaleDateString()}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
