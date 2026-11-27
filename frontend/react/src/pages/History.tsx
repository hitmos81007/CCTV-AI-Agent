import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchCases } from "../api";
import { EmptyState, ErrorBanner, PageHeader, StatusChip } from "../components/Ui";

export function History() {
  const [search, setSearch] = useState("");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["cases"],
    queryFn: fetchCases,
  });

  const filtered = (data ?? []).filter(
    (c) =>
      !search.trim() ||
      c.case_id.toLowerCase().includes(search.toLowerCase()) ||
      (c.title || "").toLowerCase().includes(search.toLowerCase()) ||
      c.description.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div>
      <PageHeader
        title="Investigation History"
        subtitle="Cases owned by your verified officer account"
      />
      {error && <ErrorBanner message={(error as Error).message} onRetry={() => refetch()} />}

      <div className="mb-4">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter your investigations..."
          className="w-full max-w-md rounded-lg border border-[#33465A] bg-[#142133] px-3 py-2 text-sm text-[#F1F5F9]"
        />
      </div>

      {!isLoading && !error && filtered.length === 0 ? (
        <EmptyState
          title="No matching investigations"
          description="Only investigations created under your approved officer account appear here."
        />
      ) : (
        <div className="space-y-2.5">
          {filtered.map((c) => (
            <Link
              key={c.case_id}
              to={`/cases/${encodeURIComponent(c.case_id)}`}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#33465A] bg-[#142133] p-4 hover:bg-[#1B2A3D]/60"
            >
              <div className="min-w-0">
                <div className="font-medium text-[#F1F5F9]">{c.title || c.case_id}</div>
                <div className="mt-1 truncate text-xs text-[#A8B6C9]">{c.description}</div>
              </div>
              <div className="flex items-center gap-3 text-xs">
                <StatusChip status={c.status} />
                <span className="tabular-nums text-[#A8B6C9]">
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
