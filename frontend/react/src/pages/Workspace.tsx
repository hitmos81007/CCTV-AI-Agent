import React from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchCase, fetchCaseProgress } from "../api";
import { ErrorBanner, PageHeader, StatusChip } from "../components/Ui";

export function Workspace() {
  const { id = "" } = useParams();
  const caseQuery = useQuery({
    queryKey: ["case", id],
    queryFn: () => fetchCase(id),
    enabled: Boolean(id),
  });
  const progressQuery = useQuery({
    queryKey: ["progress", id],
    queryFn: () => fetchCaseProgress(id),
    enabled: Boolean(id),
  });

  const c = caseQuery.data;
  const p = progressQuery.data;

  return (
    <div>
      <PageHeader
        title={c?.title || id}
        subtitle={c?.description || "Investigation workspace"}
        action={c ? <StatusChip status={c.status} /> : undefined}
      />

      {caseQuery.error && (
        <ErrorBanner
          message={(caseQuery.error as Error).message}
          onRetry={() => caseQuery.refetch()}
        />
      )}

      <div className="rounded-xl border border-[#33465A] bg-[#142133] p-6 text-sm text-[#A8B6C9]">
        <div className="font-medium text-[#F1F5F9]">Workspace Status: {p?.status ?? c?.status ?? "draft"}</div>
        <p className="mt-2 text-xs">
          Recorded findings: <span className="tabular-nums text-[#F1F5F9]">{p?.sighting_count ?? 0}</span> •
          Persisted jobs: <span className="tabular-nums text-[#F1F5F9]">{p?.jobs.length ?? 0}</span>.
          Multi-video batch upload and live worker orchestration connect in Phases 4–6.
        </p>
      </div>
    </div>
  );
}
