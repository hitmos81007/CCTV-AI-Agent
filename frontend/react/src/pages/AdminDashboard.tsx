import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, ShieldCheck } from "lucide-react";
import {
  fetchAdminApplicationDetail,
  fetchAdminApplications,
  fetchAdminHistory,
  fetchAdminOverview,
  fetchVerificationDocumentBlob,
  submitAdminDecision,
} from "../api";
import {
  ConfirmModal,
  EmptyState,
  ErrorBanner,
  MetricCard,
  PageHeader,
  StatusChip,
} from "../components/Ui";
import type { AdminDecisionType } from "../types";

export function AdminDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") || "overview";
  const queryClient = useQueryClient();

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewMime, setPreviewMime] = useState("");
  const [actionError, setActionError] = useState("");
  const [modalAction, setModalAction] = useState<{
    decision: AdminDecisionType;
    title: string;
    description: string;
    confirmLabel: string;
    requireReason: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const overviewQuery = useQuery({
    queryKey: ["admin", "overview"],
    queryFn: fetchAdminOverview,
  });

  const statusFilter =
    tab === "overview" || tab === "history" ? undefined : tab;

  const appsQuery = useQuery({
    queryKey: ["admin", "applications", statusFilter ?? "all"],
    queryFn: () => fetchAdminApplications(statusFilter),
    enabled: tab !== "history",
  });

  const historyQuery = useQuery({
    queryKey: ["admin", "history"],
    queryFn: fetchAdminHistory,
    enabled: tab === "history",
  });

  const detailQuery = useQuery({
    queryKey: ["admin", "detail", selectedUserId],
    queryFn: () => fetchAdminApplicationDetail(selectedUserId!),
    enabled: Boolean(selectedUserId) && tab !== "history",
  });

  useEffect(() => {
    const items = appsQuery.data?.items ?? [];
    if (items.length > 0 && (!selectedUserId || !items.some((i) => i.user_id === selectedUserId))) {
      setSelectedUserId(items[0].user_id);
    } else if (items.length === 0) {
      setSelectedUserId(null);
    }
  }, [appsQuery.data, selectedUserId]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const handlePreview = async (docId: string, mime: string) => {
    setActionError("");
    try {
      const blob = await fetchVerificationDocumentBlob(docId);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(URL.createObjectURL(blob));
      setPreviewMime(mime);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to load verification document.");
    }
  };

  const handleDecision = async (reason: string) => {
    if (!modalAction || !selectedUserId) return;
    setBusy(true);
    setActionError("");
    try {
      await submitAdminDecision(selectedUserId, {
        decision: modalAction.decision,
        reason: reason || undefined,
        request_key: crypto.randomUUID(),
      });
      setModalAction(null);
      await queryClient.invalidateQueries({ queryKey: ["admin"] });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to record decision.");
    } finally {
      setBusy(false);
    }
  };

  const overview = overviewQuery.data;
  const detail = detailQuery.data;
  const currentStatus = detail?.profile.verification_status;

  return (
    <div>
      <PageHeader
        title="District Administration"
        subtitle={
          overview
            ? `Authorized District: ${overview.district_id} • Administrative verification workspace`
            : "District-scoped officer verification and access control"
        }
      />

      {overviewQuery.error && (
        <ErrorBanner
          message={(overviewQuery.error as Error).message}
          onRetry={() => overviewQuery.refetch()}
        />
      )}
      {actionError && <ErrorBanner message={actionError} />}

      <div className="mb-5 rounded-xl border border-[#33465A] bg-[#142133]/70 px-4 py-3 text-xs text-[#A8B6C9]">
        <strong className="text-[#F1F5F9]">Scope Notice:</strong> Administrative approval authorizes
        officer platform access within the assigned district only; it is not automated proof of legal
        identity. District Administrators do not receive access to officers&apos; investigation footage or
        case findings.
      </div>

      {/* Metrics */}
      {overview && (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label="Pending Review" value={overview.counts.pending_review ?? 0} />
          <MetricCard label="Info Required" value={overview.counts.additional_info_required ?? 0} />
          <MetricCard label="Approved Officers" value={overview.counts.approved ?? 0} />
          <MetricCard label="Rejected" value={overview.counts.rejected ?? 0} />
          <MetricCard label="Suspended" value={overview.counts.suspended ?? 0} />
        </div>
      )}

      {/* Filter Tabs */}
      <div className="mb-5 flex flex-wrap gap-2">
        {[
          { id: "overview", label: "All Applications" },
          { id: "pending_review", label: "Pending" },
          { id: "additional_info_required", label: "Info Required" },
          { id: "approved", label: "Approved" },
          { id: "rejected", label: "Rejected" },
          { id: "suspended", label: "Suspended" },
          { id: "history", label: "Audit History" },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setSearchParams(item.id === "overview" ? {} : { tab: item.id })}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === item.id
                ? "border-[#5EE0CA] bg-[#5EE0CA]/15 text-[#5EE0CA]"
                : "border-[#33465A] bg-[#142133] text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "history" ? (
        <div className="rounded-xl border border-[#33465A] bg-[#142133] p-5">
          <h2 className="mb-4 text-base font-semibold">Administrative Decision History</h2>
          {(historyQuery.data?.items ?? []).length === 0 ? (
            <EmptyState
              title="No administrative decisions recorded yet"
              description="Approved, rejected, info-requested, and suspension actions in your district appear here."
            />
          ) : (
            <div className="space-y-2.5">
              {historyQuery.data?.items.map((rev) => (
                <div
                  key={rev.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#33465A] bg-[#0B111C] p-3.5 text-xs"
                >
                  <div>
                    <div className="font-mono text-[#F1F5F9]">Applicant: {rev.applicant_user_id}</div>
                    <div className="mt-1 text-[#A8B6C9]">
                      {rev.previous_status} → {rev.new_status}
                      {rev.reason ? ` • Reason: ${rev.reason}` : ""}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <StatusChip status={rev.new_status} />
                    <span className="text-[#A8B6C9]">{new Date(rev.created_at).toLocaleString()}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-12">
          {/* Left: Application List */}
          <div className="lg:col-span-5">
            <div className="rounded-xl border border-[#33465A] bg-[#142133] p-4">
              <h2 className="mb-3 text-sm font-semibold text-[#F1F5F9]">Applications</h2>
              {(appsQuery.data?.items ?? []).length === 0 ? (
                <EmptyState
                  title="No matching applications"
                  description="No officer verification applications match this filter in your district."
                />
              ) : (
                <div className="space-y-2">
                  {appsQuery.data?.items.map((item) => (
                    <button
                      key={item.user_id}
                      type="button"
                      onClick={() => {
                        setSelectedUserId(item.user_id);
                        if (previewUrl) {
                          URL.revokeObjectURL(previewUrl);
                          setPreviewUrl(null);
                        }
                      }}
                      className={`w-full rounded-lg border p-3.5 text-left transition-colors ${
                        selectedUserId === item.user_id
                          ? "border-[#5EE0CA] bg-[#1B2A3D]"
                          : "border-[#33465A] bg-[#0B111C] hover:bg-[#1B2A3D]/50"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-[#F1F5F9]">
                          {item.full_name}
                        </span>
                        <StatusChip status={item.verification_status} />
                      </div>
                      <div className="mt-1 truncate text-xs text-[#A8B6C9]">
                        {item.designation} • {item.police_station}
                      </div>
                      <div className="mt-1 text-[11px] text-[#A8B6C9]">
                        {item.document_count} document(s) •{" "}
                        {item.submitted_at ? new Date(item.submitted_at).toLocaleDateString() : "Draft"}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right: Verification Workspace */}
          <div className="lg:col-span-7">
            {!detail ? (
              <EmptyState
                title="Select an application"
                description="Choose an officer application from the left panel to inspect credentials and record a decision."
              />
            ) : (
              <div className="space-y-5 rounded-xl border border-[#33465A] bg-[#142133] p-6">
                <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[#33465A] pb-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="h-5 w-5 text-[#5EE0CA]" />
                      <h2 className="text-lg font-semibold text-[#F1F5F9]">
                        {detail.profile.full_name}
                      </h2>
                    </div>
                    <p className="mt-1 text-xs text-[#A8B6C9]">
                      {detail.profile.designation} • {detail.profile.police_station}
                    </p>
                  </div>
                  <StatusChip status={detail.profile.verification_status} />
                </div>

                <div className="grid gap-3 rounded-lg border border-[#33465A] bg-[#0B111C] p-4 text-xs sm:grid-cols-2">
                  <div>
                    <span className="text-[#A8B6C9]">Claimed District</span>
                    <div className="mt-0.5 font-medium text-[#F1F5F9]">
                      {detail.profile.claimed_district_id}
                    </div>
                  </div>
                  <div>
                    <span className="text-[#A8B6C9]">Employee Reference</span>
                    <div className="mt-0.5 font-mono text-[#F1F5F9]">
                      {detail.profile.employee_ref || "Not provided"}
                    </div>
                  </div>
                  <div>
                    <span className="text-[#A8B6C9]">Submitted At</span>
                    <div className="mt-0.5 text-[#F1F5F9]">
                      {detail.profile.submitted_at
                        ? new Date(detail.profile.submitted_at).toLocaleString()
                        : "Not submitted"}
                    </div>
                  </div>
                  <div>
                    <span className="text-[#A8B6C9]">Submission Version</span>
                    <div className="mt-0.5 tabular-nums text-[#F1F5F9]">
                      v{detail.profile.submission_version}
                    </div>
                  </div>
                </div>

                {/* Supporting Documents */}
                <div>
                  <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-[#A8B6C9]">
                    Supporting Documents ({detail.documents.length})
                  </h3>
                  {detail.documents.length === 0 ? (
                    <p className="text-xs text-[#A8B6C9]">No documents attached.</p>
                  ) : (
                    <div className="space-y-2">
                      {detail.documents.map((doc) => (
                        <div
                          key={doc.id}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#33465A] bg-[#0B111C] p-3 text-xs"
                        >
                          <div className="flex items-center gap-2.5">
                            <FileText className="h-4 w-4 text-[#5EE0CA]" />
                            <div>
                              <div className="font-medium text-[#F1F5F9]">{doc.original_filename}</div>
                              <div className="text-[11px] text-[#A8B6C9]">
                                {doc.document_type.replace(/_/g, " ")} • {(doc.byte_size / 1024).toFixed(1)} KB
                              </div>
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => handlePreview(doc.id, doc.mime_type)}
                            className="rounded border border-[#33465A] px-3 py-1 text-xs font-medium text-[#5EE0CA] hover:bg-[#1B2A3D]"
                          >
                            Inspect Document
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {previewUrl && (
                    <div className="mt-3 rounded-lg border border-[#33465A] bg-[#0B111C] p-3">
                      <div className="mb-2 flex items-center justify-between text-xs text-[#A8B6C9]">
                        <span>Authorized Credential Preview</span>
                        <button
                          type="button"
                          onClick={() => {
                            URL.revokeObjectURL(previewUrl);
                            setPreviewUrl(null);
                          }}
                          className="text-[#5EE0CA] hover:underline"
                        >
                          Close
                        </button>
                      </div>
                      {previewMime === "application/pdf" ? (
                        <iframe title="Applicant Credential PDF" src={previewUrl} sandbox="" className="h-72 w-full rounded" />
                      ) : (
                        <img
                          src={previewUrl}
                          alt="Applicant supporting credential"
                          className="max-h-72 rounded object-contain"
                        />
                      )}
                    </div>
                  )}
                </div>

                {/* Action Controls */}
                <div className="flex flex-wrap gap-2.5 border-t border-[#33465A] pt-4">
                  {(currentStatus === "pending_review" || currentStatus === "additional_info_required") && (
                    <>
                      <button
                        type="button"
                        onClick={() =>
                          setModalAction({
                            decision: "approved",
                            title: "Approve Officer Access",
                            description: `Approve ${detail.profile.full_name} for district ${detail.profile.claimed_district_id}?`,
                            confirmLabel: "Approve Officer",
                            requireReason: false,
                          })
                        }
                        className="rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
                      >
                        Approve
                      </button>
                      {currentStatus === "pending_review" && (
                        <button
                          type="button"
                          onClick={() =>
                            setModalAction({
                              decision: "additional_info_required",
                              title: "Request Additional Information",
                              description: "Return this application to the officer for updated documentation.",
                              confirmLabel: "Request Info",
                              requireReason: true,
                            })
                          }
                          className="rounded-lg border border-amber-500/50 bg-amber-950/40 px-4 py-2 text-xs font-semibold text-[#F7BF62] hover:bg-amber-950/70"
                        >
                          Request Additional Info
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          setModalAction({
                            decision: "rejected",
                            title: "Reject Application",
                            description: "Reject this officer verification application with a documented reason.",
                            confirmLabel: "Reject Application",
                            requireReason: true,
                          })
                        }
                        className="rounded-lg border border-rose-500/50 bg-rose-950/40 px-4 py-2 text-xs font-semibold text-[#FF9CA9] hover:bg-rose-950/70"
                      >
                        Reject
                      </button>
                    </>
                  )}

                  {currentStatus === "approved" && (
                    <button
                      type="button"
                      onClick={() =>
                        setModalAction({
                          decision: "suspended",
                          title: "Suspend Officer Access",
                          description: "Immediately revoke investigation access for this officer.",
                          confirmLabel: "Suspend Officer",
                          requireReason: true,
                        })
                      }
                      className="rounded-lg border border-rose-500/50 bg-rose-950/40 px-4 py-2 text-xs font-semibold text-[#FF9CA9] hover:bg-rose-950/70"
                    >
                      Suspend Access
                    </button>
                  )}

                  {currentStatus === "suspended" && (
                    <button
                      type="button"
                      onClick={() =>
                        setModalAction({
                          decision: "reinstated",
                          title: "Reinstate Officer Access",
                          description: "Restore approved investigation access for this officer.",
                          confirmLabel: "Reinstate Officer",
                          requireReason: false,
                        })
                      }
                      className="rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
                    >
                      Reinstate Access
                    </button>
                  )}
                </div>

                {/* Previous Reviews */}
                {detail.reviews.length > 0 && (
                  <div className="border-t border-[#33465A] pt-4">
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#A8B6C9]">
                      Previous Review Decisions
                    </h3>
                    <div className="space-y-2">
                      {detail.reviews.map((rev) => (
                        <div key={rev.id} className="rounded-lg border border-[#33465A] bg-[#0B111C] p-3 text-xs">
                          <div className="flex items-center justify-between">
                            <StatusChip status={rev.new_status} />
                            <span className="text-[#A8B6C9]">
                              {new Date(rev.created_at).toLocaleString()}
                            </span>
                          </div>
                          {rev.reason && <p className="mt-1.5 text-[#F1F5F9]">{rev.reason}</p>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <ConfirmModal
        open={Boolean(modalAction)}
        title={modalAction?.title ?? ""}
        description={modalAction?.description ?? ""}
        confirmLabel={modalAction?.confirmLabel ?? "Confirm"}
        requireReason={modalAction?.requireReason}
        busy={busy}
        onCancel={() => setModalAction(null)}
        onConfirm={handleDecision}
      />
    </div>
  );
}
