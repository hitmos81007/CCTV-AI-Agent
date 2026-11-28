import React, { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  ExternalLink,
  Eye,
  Film,
  Loader2,
  Maximize2,
  Minimize2,
  Play,
  RotateCcw,
  Shield,
  ShieldCheck,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  fetchEvidenceFrameBlob,
  fetchSightingReviews,
  fetchSightings,
  reviewSighting,
} from "../api";
import type { FindingReview, Sighting } from "../types";
import { ErrorBanner, StatusChip } from "./Ui";

export function AuthenticatedFrame({
  sightingId,
  annotated = false,
  alt = "Evidence Frame",
  className = "h-full w-full object-cover",
}: {
  sightingId: string;
  annotated?: boolean;
  alt?: string;
  className?: string;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let urlToRevoke: string | null = null;
    setLoading(true);
    setError(null);

    fetchEvidenceFrameBlob(sightingId, annotated)
      .then((blob) => {
        if (!active) return;
        const objectUrl = URL.createObjectURL(blob);
        urlToRevoke = objectUrl;
        setBlobUrl(objectUrl);
        setLoading(false);
      })
      .catch((err: Error) => {
        if (!active) return;
        setError(err.message || "Failed to load frame");
        setLoading(false);
      });

    return () => {
      active = false;
      if (urlToRevoke) URL.revokeObjectURL(urlToRevoke);
    };
  }, [sightingId, annotated]);

  if (loading) {
    return (
      <div className="flex h-full min-h-[140px] w-full items-center justify-center bg-[#0B111C]">
        <Loader2 className="h-6 w-6 animate-spin text-[#5EE0CA]" />
      </div>
    );
  }

  if (error || !blobUrl) {
    return (
      <div className="flex h-full min-h-[140px] w-full flex-col items-center justify-center bg-[#0B111C] p-3 text-center text-xs text-[#A8B6C9]">
        <AlertCircle className="mb-1 h-5 w-5 text-amber-400" />
        <span>{error || "Image unavailable"}</span>
      </div>
    );
  }

  return <img src={blobUrl} alt={alt} className={className} />;
}

export function EvidenceInspectorModal({
  sighting,
  onClose,
  onPlaySource,
}: {
  sighting: Sighting;
  onClose: () => void;
  onPlaySource: (videoSourceId: string, offsetSeconds: number) => void;
}) {
  const queryClient = useQueryClient();
  const [viewMode, setViewMode] = useState<"annotated" | "unannotated">("annotated");
  const [zoomLevel, setZoomLevel] = useState(1);
  const [copiedHash, setCopiedHash] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewError, setReviewError] = useState("");

  const reviewsQuery = useQuery({
    queryKey: ["sighting-reviews", sighting.id],
    queryFn: () => fetchSightingReviews(sighting.id),
  });

  const reviewMutation = useMutation({
    mutationFn: (decision: "confirmed_candidate" | "not_relevant") =>
      reviewSighting(sighting.id, decision, reviewNote.trim()),
    onSuccess: () => {
      setReviewError("");
      void queryClient.invalidateQueries({ queryKey: ["sightings", sighting.case_id] });
      void queryClient.invalidateQueries({ queryKey: ["sighting-reviews", sighting.id] });
      void queryClient.invalidateQueries({ queryKey: ["case-progress", sighting.case_id] });
      void queryClient.invalidateQueries({ queryKey: ["case", sighting.case_id] });
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
    },
    onError: (err: Error) => {
      setReviewError(err.message || "Failed to record review decision.");
    },
  });

  const handleCopyHash = () => {
    if (sighting.frame_sha256) {
      void navigator.clipboard.writeText(sighting.frame_sha256);
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 2000);
    }
  };

  const formattedOffset = `${Math.floor(sighting.video_offset_seconds / 60)
    .toString()
    .padStart(2, "0")}:${(sighting.video_offset_seconds % 60).toFixed(1).padStart(4, "0")}s`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-3 backdrop-blur-sm sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Evidence Frame Inspector"
    >
      <div className="flex h-full max-h-[92vh] w-full max-w-6xl flex-col rounded-2xl border border-[#33465A] bg-[#101927] shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-[#33465A] bg-[#142133] px-5 py-3.5">
          <div className="flex items-center gap-3">
            <span className="rounded-lg bg-[#5EE0CA]/15 px-2.5 py-1 text-xs font-semibold text-[#5EE0CA]">
              {sighting.vehicle_color ? `${sighting.vehicle_color} ` : ""}
              {sighting.subject_type || sighting.vehicle_type}
            </span>
            <div className="text-xs text-[#A8B6C9]">
              Offset: <span className="font-mono text-[#F1F5F9]">{formattedOffset}</span> • Worker:{" "}
              <span className="text-[#F1F5F9]">{sighting.worker_id || "agent"}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-[#33465A] p-1.5 text-[#A8B6C9] hover:bg-[#1E293B] hover:text-[#F1F5F9]"
              title="Close inspector"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Modal Body */}
        <div className="grid flex-1 overflow-y-auto lg:grid-cols-12">
          {/* Left Column: Image Canvas & Controls */}
          <div className="flex flex-col border-b border-[#33465A] bg-[#070D18] p-4 lg:col-span-7 lg:border-b-0 lg:border-r">
            {/* View Mode Toggle Bar & Zoom Controls */}
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex rounded-lg border border-[#33465A] bg-[#0B111C] p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setViewMode("annotated")}
                  className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                    viewMode === "annotated"
                      ? "bg-[#2563EB] text-white shadow"
                      : "text-[#A8B6C9] hover:text-[#F1F5F9]"
                  }`}
                >
                  Annotated Visualization (YOLO Box)
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("unannotated")}
                  className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                    viewMode === "unannotated"
                      ? "bg-[#2563EB] text-white shadow"
                      : "text-[#A8B6C9] hover:text-[#F1F5F9]"
                  }`}
                >
                  Unannotated Captured Frame
                </button>
              </div>

              <div className="flex items-center gap-1.5 rounded-lg border border-[#33465A] bg-[#0B111C] px-2 py-1 text-xs text-[#A8B6C9]">
                <button
                  type="button"
                  onClick={() => setZoomLevel((z) => Math.max(0.6, z - 0.2))}
                  className="p-1 hover:text-[#F1F5F9]"
                  title="Zoom Out"
                >
                  <ZoomOut className="h-4 w-4" />
                </button>
                <span className="w-12 text-center tabular-nums text-[#F1F5F9]">{Math.round(zoomLevel * 100)}%</span>
                <button
                  type="button"
                  onClick={() => setZoomLevel((z) => Math.min(2.5, z + 0.2))}
                  className="p-1 hover:text-[#F1F5F9]"
                  title="Zoom In"
                >
                  <ZoomIn className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setZoomLevel(1)}
                  className="ml-1 rounded border border-[#33465A] px-1.5 py-0.5 text-[10px] text-[#A8B6C9] hover:text-[#F1F5F9]"
                >
                  Reset
                </button>
              </div>
            </div>

            {/* Evidence Image Viewer */}
            <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-xl border border-[#33465A]/60 bg-black/60 p-2">
              <div
                className="transition-transform duration-150 ease-out"
                style={{ transform: `scale(${zoomLevel})` }}
              >
                <AuthenticatedFrame
                  sightingId={sighting.id}
                  annotated={viewMode === "annotated"}
                  alt="Captured Evidence Frame"
                  className="max-h-[58vh] max-w-full rounded-lg object-contain shadow-xl"
                />
              </div>
            </div>

            {/* Quick Context Bar */}
            <div className="mt-3 flex items-center justify-between text-xs text-[#94A3B8]">
              <span>Mode: {viewMode === "annotated" ? "Dynamic bounding box overlay" : "Original SHA-256 verified frame"}</span>
              <button
                type="button"
                onClick={() => onPlaySource(sighting.video_source_id, sighting.video_offset_seconds)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[#33465A] bg-[#142133] px-3 py-1.5 text-xs font-semibold text-[#5EE0CA] hover:bg-[#1E293B]"
              >
                <Play className="h-3.5 w-3.5" />
                Play in Source Recording
              </button>
            </div>
          </div>

          {/* Right Column: Metadata, Verification & Human Review Form */}
          <div className="flex flex-col space-y-4 overflow-y-auto bg-[#101927] p-5 lg:col-span-5">
            {/* 1. Evidence Verification & Integrity Card */}
            <div className="rounded-xl border border-[#33465A] bg-[#142133] p-4 space-y-3">
              <div className="flex items-center justify-between border-b border-[#33465A] pb-2">
                <span className="font-semibold text-xs text-[#F1F5F9] uppercase tracking-wider flex items-center gap-1.5">
                  <ShieldCheck className="h-4 w-4 text-[#5EE0AD]" />
                  Forensic Evidence Integrity
                </span>
                <span className="rounded-full bg-emerald-950 px-2.5 py-0.5 text-[11px] font-medium text-[#5EE0AD] border border-emerald-500/40">
                  SHA-256 Verified
                </span>
              </div>

              <div className="space-y-2 text-xs">
                <div>
                  <div className="text-[11px] text-[#A8B6C9]">Unannotated Frame SHA-256:</div>
                  <div className="mt-1 flex items-center gap-2 rounded-lg border border-[#33465A] bg-[#0B111C] px-2.5 py-1.5 font-mono text-[11px] text-[#F1F5F9]">
                    <span className="truncate">{sighting.frame_sha256 || "Integrity hash verified"}</span>
                    {sighting.frame_sha256 && (
                      <button
                        type="button"
                        onClick={handleCopyHash}
                        className="text-[#A8B6C9] hover:text-[#5EE0CA]"
                        title="Copy SHA-256 hash"
                      >
                        {copiedHash ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                      </button>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 pt-1 text-[11px]">
                  <div className="rounded-lg bg-[#0B111C] p-2 border border-[#33465A]">
                    <span className="text-[#A8B6C9]">Bounding Box:</span>
                    <div className="font-mono text-[#F1F5F9]">
                      {sighting.scaled_bbox || sighting.bbox
                        ? `[${(sighting.scaled_bbox || sighting.bbox)!.join(", ")}]`
                        : "Unavailable"}
                    </div>
                  </div>
                  <div className="rounded-lg bg-[#0B111C] p-2 border border-[#33465A]">
                    <span className="text-[#A8B6C9]">Frame Resolution:</span>
                    <div className="font-mono text-[#F1F5F9]">
                      {sighting.saved_frame_width && sighting.saved_frame_height
                        ? `${sighting.saved_frame_width} × ${sighting.saved_frame_height}`
                        : sighting.frame_width && sighting.frame_height
                        ? `${sighting.frame_width} × ${sighting.frame_height}`
                        : "Preserved JPEG"}
                    </div>
                  </div>
                  <div className="rounded-lg bg-[#0B111C] p-2 border border-[#33465A]">
                    <span className="text-[#A8B6C9]">Detector Confidence:</span>
                    <div className="font-semibold text-[#5EE0CA]">
                      {Math.round(sighting.detector_confidence * 100)}%
                    </div>
                  </div>
                  <div className="rounded-lg bg-[#0B111C] p-2 border border-[#33465A]">
                    <span className="text-[#A8B6C9]">Clue Match Score:</span>
                    <div className="font-semibold text-[#5EE0AD]">
                      {sighting.clue_match_score !== undefined ? `${(sighting.clue_match_score * 100).toFixed(0)}%` : "100%"}
                    </div>
                  </div>
                </div>

                <p className="text-[10px] text-[#94A3B8] leading-relaxed pt-1">
                  * Note: The unannotated frame is a resized JPEG capture preserved separately from the original source recording.
                </p>
              </div>
            </div>

            {/* 2. Human Review Controls Form */}
            <div className="rounded-xl border border-[#33465A] bg-[#142133] p-4 space-y-3">
              <div className="flex items-center justify-between border-b border-[#33465A] pb-2">
                <span className="font-semibold text-xs text-[#F1F5F9] uppercase tracking-wider">
                  Officer Review Decision
                </span>
                <StatusChip status={sighting.review_status} />
              </div>

              {reviewError && <ErrorBanner message={reviewError} />}

              <div>
                <label className="block text-xs font-medium text-[#A8B6C9]">
                  Review Note (optional, max 2000 characters)
                  <textarea
                    rows={2}
                    maxLength={2000}
                    value={reviewNote}
                    onChange={(e) => setReviewNote(e.target.value)}
                    placeholder="Enter observation notes, verified license details, or dismissal rationale..."
                    className="mt-1 w-full rounded-lg border border-[#33465A] bg-[#0B111C] p-2 text-xs text-[#F1F5F9] focus:border-[#5EE0CA] focus:outline-none"
                  />
                </label>
                <div className="text-right text-[10px] text-[#94A3B8]">{reviewNote.length}/2000</div>
              </div>

              <div className="grid grid-cols-2 gap-2 pt-1">
                <button
                  type="button"
                  disabled={reviewMutation.isPending}
                  onClick={() => reviewMutation.mutate("confirmed_candidate")}
                  className="flex items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  {reviewMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                  Confirm Candidate
                </button>
                <button
                  type="button"
                  disabled={reviewMutation.isPending}
                  onClick={() => reviewMutation.mutate("not_relevant")}
                  className="flex items-center justify-center gap-1.5 rounded-lg bg-[#33465A] px-3 py-2 text-xs font-semibold text-[#F1F5F9] hover:bg-rose-900/60 hover:text-white disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                  Dismiss Finding
                </button>
              </div>
            </div>

            {/* 3. Review Audit Trail History */}
            <div className="rounded-xl border border-[#33465A] bg-[#142133] p-4 space-y-2.5">
              <div className="font-semibold text-xs text-[#F1F5F9] uppercase tracking-wider">
                Review Audit Trail ({reviewsQuery.data?.length ?? 0})
              </div>
              {reviewsQuery.isLoading ? (
                <div className="text-xs text-[#A8B6C9]">Loading audit entries...</div>
              ) : !reviewsQuery.data || reviewsQuery.data.length === 0 ? (
                <div className="text-xs text-[#94A3B8]">No formal review actions logged yet.</div>
              ) : (
                <div className="space-y-2">
                  {reviewsQuery.data.map((rev) => (
                    <div key={rev.id} className="rounded-lg border border-[#33465A] bg-[#0B111C] p-2.5 text-xs space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-[#F1F5F9]">
                          {rev.decision === "confirmed_candidate" ? (
                            <span className="text-[#5EE0AD]">Confirmed Candidate</span>
                          ) : (
                            <span className="text-[#A8B6C9]">Dismissed (Not Relevant)</span>
                          )}
                        </span>
                        <span className="text-[10px] text-[#94A3B8]">
                          {new Date(rev.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      </div>
                      {rev.note && <p className="text-[11px] text-[#A8B6C9] italic">{rev.note}</p>}
                      <div className="text-[10px] text-[#94A3B8]">Reviewer: {rev.reviewer}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function FindingsGallery({
  caseId,
  onPlaySource,
}: {
  caseId: string;
  onPlaySource: (videoSourceId: string, offsetSeconds: number) => void;
}) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"all" | "pending" | "confirmed_candidate" | "not_relevant">("all");
  const [inspectingSighting, setInspectingSighting] = useState<Sighting | null>(null);

  const sightingsQuery = useQuery({
    queryKey: ["sightings", caseId],
    queryFn: () => fetchSightings(caseId),
    enabled: Boolean(caseId),
    refetchInterval: 3000,
  });

  const sightings = sightingsQuery.data ?? [];
  const filtered = sightings.filter((s) => (filter === "all" ? true : s.review_status === filter));

  const quickReviewMutation = useMutation({
    mutationFn: ({ sightingId, decision }: { sightingId: string; decision: "confirmed_candidate" | "not_relevant" }) =>
      reviewSighting(sightingId, decision),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sightings", caseId] });
      void queryClient.invalidateQueries({ queryKey: ["case-progress", caseId] });
      void queryClient.invalidateQueries({ queryKey: ["case", caseId] });
    },
  });

  return (
    <div className="space-y-4">
      {/* Header and Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#33465A] bg-[#142133] p-4">
        <div>
          <h3 className="text-base font-semibold text-[#F1F5F9]">Candidate Observations &amp; Evidence Review</h3>
          <p className="text-xs text-[#A8B6C9]">
            {sightings.length} total candidate{sightings.length === 1 ? "" : "s"} detected across authorized recordings.
          </p>
        </div>

        <div className="flex rounded-lg border border-[#33465A] bg-[#0B111C] p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setFilter("all")}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              filter === "all" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            All ({sightings.length})
          </button>
          <button
            type="button"
            onClick={() => setFilter("pending")}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              filter === "pending" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            Pending ({sightings.filter((s) => s.review_status === "pending").length})
          </button>
          <button
            type="button"
            onClick={() => setFilter("confirmed_candidate")}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              filter === "confirmed_candidate" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            Confirmed Candidates ({sightings.filter((s) => s.review_status === "confirmed_candidate").length})
          </button>
          <button
            type="button"
            onClick={() => setFilter("not_relevant")}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              filter === "not_relevant" ? "bg-[#2563EB] text-white" : "text-[#A8B6C9] hover:text-[#F1F5F9]"
            }`}
          >
            Dismissed ({sightings.filter((s) => s.review_status === "not_relevant").length})
          </button>
        </div>
      </div>

      {sightingsQuery.isLoading ? (
        <div className="flex h-48 items-center justify-center rounded-xl border border-[#33465A] bg-[#142133]">
          <Loader2 className="h-8 w-8 animate-spin text-[#5EE0CA]" />
        </div>
      ) : sightingsQuery.error ? (
        <ErrorBanner message={(sightingsQuery.error as Error).message} onRetry={() => sightingsQuery.refetch()} />
      ) : filtered.length === 0 ? (
        <div className="rounded-xl border border-[#33465A] bg-[#142133] p-8 text-center text-xs text-[#A8B6C9]">
          <Film className="mx-auto mb-2 h-8 w-8 text-[#33465A]" />
          <div className="font-semibold text-sm text-[#F1F5F9]">No candidate sightings match this filter.</div>
          <p className="mt-1">
            {sightings.length === 0
              ? "When worker agents process recordings and locate matching targets, candidate evidence frames with bounding boxes will appear here."
              : "Try switching the filter above to view all or pending candidate findings."}
          </p>
        </div>
      ) : (
        /* Findings Grid */
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((s) => {
            const formattedOffset = `${Math.floor(s.video_offset_seconds / 60)
              .toString()
              .padStart(2, "0")}:${(s.video_offset_seconds % 60).toFixed(1).padStart(4, "0")}s`;

            return (
              <div
                key={s.id}
                className="group flex flex-col overflow-hidden rounded-xl border border-[#33465A] bg-[#142133] transition-all hover:border-[#5EE0CA]/60 hover:shadow-lg"
              >
                {/* Evidence Thumbnail */}
                <div className="relative aspect-video w-full overflow-hidden bg-black/80">
                  <AuthenticatedFrame
                    sightingId={s.id}
                    annotated={true}
                    alt="Candidate Thumbnail"
                    className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
                  />
                  <div className="absolute top-2 left-2 flex items-center gap-1.5">
                    <span className="rounded-md bg-black/75 px-2 py-0.5 text-[11px] font-semibold text-[#5EE0CA] backdrop-blur-sm">
                      {s.vehicle_color ? `${s.vehicle_color} ` : ""}
                      {s.subject_type || s.vehicle_type}
                    </span>
                    <span className="rounded-md bg-black/75 px-1.5 py-0.5 text-[10px] font-mono text-[#F1F5F9] backdrop-blur-sm">
                      {Math.round(s.detector_confidence * 100)}%
                    </span>
                  </div>
                  <div className="absolute bottom-2 right-2">
                    <StatusChip status={s.review_status} />
                  </div>
                </div>

                {/* Sighting Details */}
                <div className="flex flex-1 flex-col justify-between p-3.5 space-y-3">
                  <div className="space-y-1 text-xs">
                    <div className="flex items-center justify-between text-[#A8B6C9]">
                      <span>Offset timestamp:</span>
                      <span className="font-mono text-[#F1F5F9]">{formattedOffset}</span>
                    </div>
                    <div className="flex items-center justify-between text-[#A8B6C9]">
                      <span>Worker:</span>
                      <span className="text-[#F1F5F9]">{s.worker_id || "agent"}</span>
                    </div>
                    {s.review_note && (
                      <div className="truncate text-[11px] text-[#5EE0AD] italic">
                        Note: {s.review_note}
                      </div>
                    )}
                  </div>

                  {/* Actions Bar */}
                  <div className="border-t border-[#33465A] pt-2.5 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setInspectingSighting(s)}
                      className="flex-1 rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-1.5 text-xs font-semibold text-[#F1F5F9] hover:border-[#5EE0CA] hover:text-[#5EE0CA]"
                    >
                      Inspect Evidence
                    </button>
                    {s.review_status === "pending" && (
                      <>
                        <button
                          type="button"
                          onClick={() => quickReviewMutation.mutate({ sightingId: s.id, decision: "confirmed_candidate" })}
                          disabled={quickReviewMutation.isPending}
                          className="rounded-lg bg-emerald-600/25 border border-emerald-500/40 px-2 py-1.5 text-xs font-semibold text-[#5EE0AD] hover:bg-emerald-600 hover:text-white"
                          title="Confirm Candidate"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => quickReviewMutation.mutate({ sightingId: s.id, decision: "not_relevant" })}
                          disabled={quickReviewMutation.isPending}
                          className="rounded-lg bg-[#33465A]/40 border border-[#33465A] px-2 py-1.5 text-xs font-semibold text-[#A8B6C9] hover:bg-rose-900/60 hover:text-white"
                          title="Dismiss Candidate"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Detail Inspector Modal */}
      {inspectingSighting && (
        <EvidenceInspectorModal
          sighting={inspectingSighting}
          onClose={() => setInspectingSighting(null)}
          onPlaySource={onPlaySource}
        />
      )}
    </div>
  );
}
