import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Film,
  Loader2,
  Play,
  RotateCcw,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import {
  createCase,
  deleteDraftVideo,
  fetchCase,
  fetchVideoPlaybackBlob,
  updateCaseDraft,
  uploadCaseVideo,
} from "../api";
import { ErrorBanner, PageHeader, StatusChip } from "./Ui";
import type { CaseVideo } from "../types";

type QueueStatus = "selected" | "uploading" | "validating" | "ready" | "failed" | "cancelled";

interface QueuedClip {
  localId: string;
  uploadKey: string;
  file?: File;
  sourceLabel: string;
  recordedAt: string;
  status: QueueStatus;
  progress: number;
  error?: string;
  persisted?: CaseVideo;
  abortController?: AbortController;
}

const EXAMPLE_PROMPTS = [
  "Find a red car in the uploaded recordings.",
  "Locate appearances of a blue motorcycle.",
  "Find matching observations of a white delivery truck.",
];

const SUPPORTED_EXT_RE = /\.(mp4|mov|mkv|avi|webm|m4v)$/i;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "0.0s";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

function makeUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `k-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function InvestigationWorkspace({ initialCaseId = "" }: { initialCaseId?: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [activeCaseId, setActiveCaseId] = useState(initialCaseId);
  const [createIdemKey] = useState(() => `case-${makeUuid()}`);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [clueNotes, setClueNotes] = useState("");
  const [incidentTimeText, setIncidentTimeText] = useState("");
  const [authorized, setAuthorized] = useState(false);

  const [savingDraft, setSavingDraft] = useState(false);
  const [formError, setFormError] = useState("");
  const [formNotice, setFormNotice] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const [clips, setClips] = useState<QueuedClip[]>([]);
  const [previewClip, setPreviewClip] = useState<{ title: string; url: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");

  useEffect(() => {
    setActiveCaseId(initialCaseId);
  }, [initialCaseId]);

  const caseQuery = useQuery({
    queryKey: ["case", activeCaseId],
    queryFn: () => fetchCase(activeCaseId),
    enabled: Boolean(activeCaseId),
  });

  useEffect(() => {
    const c = caseQuery.data;
    if (!c) return;
    setTitle(c.title ?? "");
    setDescription(c.description ?? "");
    setClueNotes(c.clues?.clue_notes ?? "");
    setIncidentTimeText(c.clues?.incident_time_text ?? "");
    setAuthorized(Boolean(c.authorized_at));

    const persistedVideos = c.videos ?? [];
    setClips((prev) => {
      const unsaved = prev.filter((item) => item.status !== "ready" && !item.persisted);
      const readyItems: QueuedClip[] = persistedVideos.map((v) => ({
        localId: v.video_source_id,
        uploadKey: v.video_source_id,
        sourceLabel: v.source_label || v.original_name,
        recordedAt: v.recorded_at ? v.recorded_at.slice(0, 16) : "",
        status: "ready",
        progress: 100,
        persisted: v,
      }));
      return [...readyItems, ...unsaved];
    });
  }, [caseQuery.data]);

  useEffect(() => {
    return () => {
      if (previewClip?.url) URL.revokeObjectURL(previewClip.url);
    };
  }, [previewClip]);

  const maxUploadBytes = caseQuery.data?.limits?.max_upload_bytes ?? 200 * 1024 * 1024;
  const maxCaseVideos = caseQuery.data?.limits?.max_case_videos ?? 10;
  const caseStatus = caseQuery.data?.status ?? "draft";
  const isDraft = !activeCaseId || caseStatus === "draft";

  const handleSaveDraft = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setFormNotice("");
    if (title.trim().length < 2) {
      setFormError("Enter an investigation title (at least 2 characters).");
      return;
    }
    if (description.trim().length < 3) {
      setFormError("Enter an incident description.");
      return;
    }
    if (!authorized) {
      setFormError("Confirm that all footage for this investigation is authorized.");
      return;
    }
    setSavingDraft(true);
    try {
      if (!activeCaseId) {
        const created = await createCase(
          {
            title: title.trim(),
            description: description.trim(),
            clue_notes: clueNotes.trim() || null,
            incident_time_text: incidentTimeText.trim() || null,
            authorized: true,
          },
          createIdemKey
        );
        setActiveCaseId(created.case_id);
        setFormNotice(`Draft investigation ${created.case_id} saved. You can now upload CCTV recordings.`);
        await queryClient.invalidateQueries({ queryKey: ["cases"] });
        navigate(`/cases/${encodeURIComponent(created.case_id)}`, { replace: true });
      } else {
        await updateCaseDraft(activeCaseId, {
          title: title.trim(),
          description: description.trim(),
          clue_notes: clueNotes.trim() || null,
          incident_time_text: incidentTimeText.trim() || null,
        });
        setFormNotice("Investigation draft details updated.");
        await queryClient.invalidateQueries({ queryKey: ["case", activeCaseId] });
        await queryClient.invalidateQueries({ queryKey: ["cases"] });
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save investigation draft.");
    } finally {
      setSavingDraft(false);
    }
  };

  const addFiles = (fileList: FileList | File[]) => {
    setFormError("");
    const incoming = Array.from(fileList);
    if (incoming.length === 0) return;

    setClips((prev) => {
      const remainingSlots = Math.max(0, maxCaseVideos - prev.length);
      const next: QueuedClip[] = [];
      for (const file of incoming.slice(0, remainingSlots)) {
        const localId = makeUuid();
        let error: string | undefined;
        let status: QueueStatus = "selected";
        if (!SUPPORTED_EXT_RE.test(file.name)) {
          status = "failed";
          error = "Unsupported format. Use MP4, MOV, MKV, AVI, WebM, or M4V.";
        } else if (file.size <= 0) {
          status = "failed";
          error = "Selected file is empty.";
        } else if (file.size > maxUploadBytes) {
          status = "failed";
          error = `File exceeds ${Math.round(maxUploadBytes / (1024 * 1024))} MB limit.`;
        }
        next.push({
          localId,
          uploadKey: `vid-${localId}`,
          file,
          sourceLabel: file.name.replace(/\.[^.]+$/, ""),
          recordedAt: "",
          status,
          progress: 0,
          error,
        });
      }
      if (incoming.length > remainingSlots) {
        setFormError(`Maximum of ${maxCaseVideos} videos per investigation; extra files were skipped.`);
      }
      return [...prev, ...next];
    });
  };

  const ensureDraftExists = async (): Promise<string | null> => {
    if (activeCaseId) return activeCaseId;
    if (title.trim().length < 2 || description.trim().length < 3 || !authorized) {
      setFormError("Complete Investigation Title, Incident Description, and Authorization Confirmation before uploading.");
      return null;
    }
    setSavingDraft(true);
    try {
      const created = await createCase(
        {
          title: title.trim(),
          description: description.trim(),
          clue_notes: clueNotes.trim() || null,
          incident_time_text: incidentTimeText.trim() || null,
          authorized: true,
        },
        createIdemKey
      );
      setActiveCaseId(created.case_id);
      await queryClient.invalidateQueries({ queryKey: ["cases"] });
      navigate(`/cases/${encodeURIComponent(created.case_id)}`, { replace: true });
      return created.case_id;
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save investigation draft before upload.");
      return null;
    } finally {
      setSavingDraft(false);
    }
  };

  const uploadSingleClip = async (clip: QueuedClip, targetCaseId?: string) => {
    if (!clip.file || clip.status === "uploading" || clip.status === "validating" || clip.status === "ready") {
      return;
    }
    const caseId = targetCaseId || (await ensureDraftExists());
    if (!caseId) return;

    const controller = new AbortController();
    setClips((prev) =>
      prev.map((item) =>
        item.localId === clip.localId
          ? { ...item, status: "uploading", progress: 0, error: undefined, abortController: controller }
          : item
      )
    );

    try {
      const recordedIso = clip.recordedAt.trim() ? new Date(clip.recordedAt).toISOString() : null;
      const uploaded = await uploadCaseVideo(caseId, clip.file, {
        uploadKey: clip.uploadKey,
        sourceLabel: clip.sourceLabel,
        recordedAt: recordedIso,
        signal: controller.signal,
        onProgress: (pct) => {
          setClips((prev) =>
            prev.map((item) =>
              item.localId === clip.localId
                ? {
                    ...item,
                    progress: pct,
                    status: pct >= 100 ? "validating" : "uploading",
                  }
                : item
            )
          );
        },
      });
      setClips((prev) =>
        prev.map((item) =>
          item.localId === clip.localId
            ? {
                ...item,
                status: "ready",
                progress: 100,
                persisted: uploaded,
                abortController: undefined,
                error: undefined,
              }
            : item
        )
      );
      await queryClient.invalidateQueries({ queryKey: ["case", caseId] });
      await queryClient.invalidateQueries({ queryKey: ["cases"] });
    } catch (err) {
      const cancelled = controller.signal.aborted;
      const message = err instanceof Error ? err.message : "Upload failed.";
      setClips((prev) =>
        prev.map((item) =>
          item.localId === clip.localId
            ? {
                ...item,
                status: cancelled ? "cancelled" : "failed",
                error: cancelled ? "Upload cancelled." : message,
                abortController: undefined,
              }
            : item
        )
      );
    }
  };

  const uploadAllPending = async () => {
    const caseId = await ensureDraftExists();
    if (!caseId) return;
    const pending = clips.filter((c) => c.status === "selected" || c.status === "failed" || c.status === "cancelled");
    for (const clip of pending) {
      await uploadSingleClip(clip, caseId);
    }
  };

  const handleRemoveClip = async (clip: QueuedClip) => {
    if (clip.status === "uploading" && clip.abortController) {
      clip.abortController.abort();
      return;
    }
    if (clip.persisted && isDraft) {
      try {
        await deleteDraftVideo(clip.persisted.video_source_id);
        setClips((prev) => prev.filter((item) => item.localId !== clip.localId));
        if (activeCaseId) {
          await queryClient.invalidateQueries({ queryKey: ["case", activeCaseId] });
          await queryClient.invalidateQueries({ queryKey: ["cases"] });
        }
      } catch (err) {
        setFormError(err instanceof Error ? err.message : "Failed to remove draft video.");
      }
      return;
    }
    setClips((prev) => prev.filter((item) => item.localId !== clip.localId));
  };

  const handleOpenPreview = async (clip: QueuedClip) => {
    if (!clip.persisted) return;
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const blob = await fetchVideoPlaybackBlob(clip.persisted.video_source_id);
      if (previewClip?.url) URL.revokeObjectURL(previewClip.url);
      setPreviewClip({
        title: clip.persisted.source_label || clip.persisted.original_name,
        url: URL.createObjectURL(blob),
      });
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Unable to preview video.");
    } finally {
      setPreviewLoading(false);
    }
  };

  const stats = useMemo(() => {
    const ready = clips.filter((c) => c.status === "ready");
    const failed = clips.filter((c) => c.status === "failed");
    const inFlight = clips.filter((c) => c.status === "uploading" || c.status === "validating");
    const queued = clips.filter((c) => c.status === "selected" || c.status === "cancelled");
    const totalDuration = ready.reduce((acc, c) => acc + (c.persisted?.duration_seconds ?? 0), 0);
    const totalBytes = ready.reduce((acc, c) => acc + (c.persisted?.byte_size ?? 0), 0);
    return {
      total: clips.length,
      readyCount: ready.length,
      failedCount: failed.length,
      inFlightCount: inFlight.length,
      queuedCount: queued.length,
      totalDuration,
      totalBytes,
    };
  }, [clips]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={activeCaseId ? title || activeCaseId : "New Investigation"}
        subtitle={
          activeCaseId
            ? `Case ID: ${activeCaseId} • Persisted draft & multi-video ingestion workspace`
            : "Create an authorized investigation and upload demonstration CCTV recordings"
        }
        action={activeCaseId ? <StatusChip status={caseStatus} /> : undefined}
      />

      {caseQuery.error && (
        <ErrorBanner message={(caseQuery.error as Error).message} onRetry={() => caseQuery.refetch()} />
      )}
      {formError && <ErrorBanner message={formError} />}
      {formNotice && (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-950/30 px-4 py-3 text-xs text-[#5EE0AD]">
          {formNotice}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-12">
        {/* Left column: Investigation Information + Video Upload Workspace */}
        <div className="space-y-6 lg:col-span-8">
          {/* A. Investigation Information */}
          <form onSubmit={handleSaveDraft} className="rounded-xl border border-[#33465A] bg-[#142133] p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
              <div>
                <h2 className="text-base font-semibold text-[#F1F5F9]">A. Investigation Information</h2>
                <p className="text-xs text-[#A8B6C9]">
                  No manual camera network, NVR, or zone JSON setup required.
                </p>
              </div>
              <ShieldCheck className="h-5 w-5 text-[#5EE0CA]" aria-hidden="true" />
            </div>

            <label className="block text-xs font-medium text-[#A8B6C9]">
              Investigation Title
              <input
                type="text"
                required
                minLength={2}
                maxLength={160}
                disabled={!isDraft}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Example: Central Corridor Vehicle Observation"
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9] disabled:opacity-60"
              />
            </label>

            <div>
              <label className="block text-xs font-medium text-[#A8B6C9]">
                Incident Description
                <textarea
                  rows={3}
                  required
                  minLength={3}
                  disabled={!isDraft}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Describe the target vehicle or visible object to locate..."
                  className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9] disabled:opacity-60"
                />
              </label>
              {isDraft && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {EXAMPLE_PROMPTS.map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => setDescription(ex)}
                      className="rounded-md border border-[#33465A] bg-[#0B111C] px-2.5 py-1 text-[11px] text-[#A8B6C9] hover:border-[#5EE0CA]/50 hover:text-[#5EE0CA]"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-xs font-medium text-[#A8B6C9]">
                Visual Clue Details (optional)
                <input
                  type="text"
                  disabled={!isDraft}
                  value={clueNotes}
                  onChange={(e) => setClueNotes(e.target.value)}
                  placeholder="Example: Red sedan, roof rack"
                  className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9] disabled:opacity-60"
                />
              </label>
              <label className="block text-xs font-medium text-[#A8B6C9]">
                Approximate Incident Date &amp; Time (optional)
                <input
                  type="text"
                  disabled={!isDraft}
                  value={incidentTimeText}
                  onChange={(e) => setIncidentTimeText(e.target.value)}
                  placeholder="Example: Nov 27, 10:15 AM"
                  className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9] disabled:opacity-60"
                />
              </label>
            </div>

            <label className="flex items-start gap-2.5 rounded-lg border border-[#33465A] bg-[#0B111C] p-3 text-xs text-[#A8B6C9]">
              <input
                type="checkbox"
                disabled={!isDraft}
                checked={authorized}
                onChange={(e) => setAuthorized(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                I confirm this investigation uses synthetic, staged, or expressly authorized footage only
                (no facial recognition, biometric tracking, or automated plate OCR).
              </span>
            </label>

            {isDraft && (
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={savingDraft}
                  className="rounded-lg bg-[#5EE0CA] px-5 py-2.5 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
                >
                  {savingDraft
                    ? "Saving Draft..."
                    : activeCaseId
                      ? "Update Draft Details"
                      : "Save Investigation Draft"}
                </button>
              </div>
            )}
          </form>

          {/* B. Video Upload Workspace */}
          <section className="rounded-xl border border-[#33465A] bg-[#142133] p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#33465A] pb-3">
              <div>
                <h2 className="text-base font-semibold text-[#F1F5F9]">B. CCTV Video Upload Workspace</h2>
                <p className="text-xs text-[#A8B6C9]">
                  Supported: MP4, MOV, MKV, AVI, WebM, M4V • Max {Math.round(maxUploadBytes / (1024 * 1024))} MB each • Up to {maxCaseVideos} clips
                </p>
              </div>
              {(stats.queuedCount > 0 || stats.failedCount > 0) && (
                <button
                  type="button"
                  onClick={uploadAllPending}
                  disabled={savingDraft || stats.inFlightCount > 0}
                  className="rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
                >
                  Upload Pending ({stats.queuedCount + stats.failedCount})
                </button>
              )}
            </div>

            {isDraft && (
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
                }}
                className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
                  dragOver
                    ? "border-[#5EE0CA] bg-[#5EE0CA]/10"
                    : "border-[#33465A] bg-[#0B111C] hover:border-[#5EE0CA]/50"
                }`}
              >
                <UploadCloud className="h-8 w-8 text-[#5EE0CA]" aria-hidden="true" />
                <p className="mt-2 text-sm font-medium text-[#F1F5F9]">
                  Drag and drop multiple CCTV recordings here
                </p>
                <p className="mt-1 text-xs text-[#A8B6C9]">
                  Each recording uploads independently with SHA-256 hashing and FFprobe/OpenCV validation.
                </p>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-4 rounded-lg border border-[#33465A] bg-[#142133] px-4 py-2 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
                >
                  Select Video Files
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,video/*"
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files) addFiles(e.target.files);
                    e.target.value = "";
                  }}
                />
              </div>
            )}

            {previewError && <ErrorBanner message={previewError} />}

            {clips.length === 0 ? (
              <div className="rounded-lg border border-[#33465A] bg-[#0B111C] p-6 text-center text-xs text-[#A8B6C9]">
                No recordings selected or uploaded yet. Select one or more synthetic/authorized clips above.
              </div>
            ) : (
              <div className="space-y-3">
                {clips.map((clip) => (
                  <div
                    key={clip.localId}
                    className="rounded-xl border border-[#33465A] bg-[#0B111C] p-4 space-y-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-start gap-3 min-w-0">
                        <Film className="mt-0.5 h-4 w-4 shrink-0 text-[#5EE0CA]" aria-hidden="true" />
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium text-[#F1F5F9]">
                            {clip.persisted?.original_name ?? clip.file?.name ?? clip.sourceLabel}
                          </div>
                          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[#A8B6C9] tabular-nums">
                            <span>{formatBytes(clip.persisted?.byte_size ?? clip.file?.size ?? 0)}</span>
                            {clip.persisted && (
                              <>
                                <span>{formatDuration(clip.persisted.duration_seconds)}</span>
                                {clip.persisted.width && clip.persisted.height && (
                                  <span>
                                    {clip.persisted.width}×{clip.persisted.height}
                                  </span>
                                )}
                                {clip.persisted.fps && <span>{clip.persisted.fps} fps</span>}
                                <span>SHA-256: {clip.persisted.sha256.slice(0, 12)}…</span>
                              </>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {clip.status === "ready" && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-950/60 px-2.5 py-0.5 text-[11px] font-medium text-[#5EE0AD]">
                            <CheckCircle2 className="h-3 w-3" /> Ready
                          </span>
                        )}
                        {clip.status === "selected" && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-slate-500/40 bg-slate-800/70 px-2.5 py-0.5 text-[11px] text-[#A8B6C9]">
                            <Clock className="h-3 w-3" /> Selected
                          </span>
                        )}
                        {(clip.status === "uploading" || clip.status === "validating") && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 px-2.5 py-0.5 text-[11px] text-[#5EE0CA]">
                            <Loader2 className="h-3 w-3 animate-spin" />{" "}
                            {clip.status === "validating" ? "Validating" : `${clip.progress}%`}
                          </span>
                        )}
                        {(clip.status === "failed" || clip.status === "cancelled") && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-950/60 px-2.5 py-0.5 text-[11px] text-[#FF9CA9]">
                            <AlertCircle className="h-3 w-3" />{" "}
                            {clip.status === "cancelled" ? "Cancelled" : "Failed"}
                          </span>
                        )}
                      </div>
                    </div>

                    {!clip.persisted && (
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="block text-[11px] text-[#A8B6C9]">
                          Source Label (optional)
                          <input
                            type="text"
                            disabled={clip.status === "uploading" || clip.status === "validating"}
                            value={clip.sourceLabel}
                            onChange={(e) =>
                              setClips((prev) =>
                                prev.map((it) =>
                                  it.localId === clip.localId ? { ...it, sourceLabel: e.target.value } : it
                                )
                              )
                            }
                            className="mt-1 w-full rounded-md border border-[#33465A] bg-[#142133] px-2.5 py-1.5 text-xs text-[#F1F5F9]"
                          />
                        </label>
                        <label className="block text-[11px] text-[#A8B6C9]">
                          Recording Timestamp (optional; blank = unknown)
                          <input
                            type="datetime-local"
                            disabled={clip.status === "uploading" || clip.status === "validating"}
                            value={clip.recordedAt}
                            onChange={(e) =>
                              setClips((prev) =>
                                prev.map((it) =>
                                  it.localId === clip.localId ? { ...it, recordedAt: e.target.value } : it
                                )
                              )
                            }
                            className="mt-1 w-full rounded-md border border-[#33465A] bg-[#142133] px-2.5 py-1.5 text-xs text-[#F1F5F9]"
                          />
                        </label>
                      </div>
                    )}

                    {(clip.status === "uploading" || clip.status === "validating") && (
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#142133]">
                        <div
                          className="h-full bg-[#5EE0CA] transition-all"
                          style={{ width: `${clip.progress}%` }}
                        />
                      </div>
                    )}

                    {clip.error && <p className="text-xs text-[#FF9CA9]">{clip.error}</p>}

                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {clip.status === "selected" && (
                        <button
                          type="button"
                          onClick={() => uploadSingleClip(clip)}
                          className="rounded-lg bg-[#5EE0CA] px-3 py-1.5 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
                        >
                          Upload Clip
                        </button>
                      )}
                      {(clip.status === "failed" || clip.status === "cancelled") && clip.file && (
                        <button
                          type="button"
                          onClick={() => uploadSingleClip(clip)}
                          className="inline-flex items-center gap-1 rounded-lg border border-[#5EE0CA]/50 px-3 py-1.5 text-xs font-medium text-[#5EE0CA] hover:bg-[#5EE0CA]/10"
                        >
                          <RotateCcw className="h-3.5 w-3.5" /> Retry
                        </button>
                      )}
                      {clip.status === "ready" && clip.persisted && (
                        <button
                          type="button"
                          disabled={previewLoading}
                          onClick={() => handleOpenPreview(clip)}
                          className="inline-flex items-center gap-1 rounded-lg border border-[#33465A] bg-[#142133] px-3 py-1.5 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
                        >
                          <Play className="h-3.5 w-3.5 text-[#5EE0CA]" /> Preview
                        </button>
                      )}
                      {(isDraft || clip.status !== "ready") && (
                        <button
                          type="button"
                          onClick={() => handleRemoveClip(clip)}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-500/30 px-3 py-1.5 text-xs font-medium text-[#FF9CA9] hover:bg-rose-950/40"
                        >
                          {clip.status === "uploading" ? (
                            <>
                              <X className="h-3.5 w-3.5" /> Cancel
                            </>
                          ) : (
                            <>
                              <Trash2 className="h-3.5 w-3.5" /> Remove
                            </>
                          )}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Right column: C. Readiness Panel + D. Submission */}
        <aside className="space-y-6 lg:col-span-4">
          <div className="rounded-xl border border-[#33465A] bg-[#142133] p-6 space-y-4">
            <h2 className="border-b border-[#33465A] pb-3 text-base font-semibold text-[#F1F5F9]">
              C. Investigation Readiness
            </h2>
            <dl className="space-y-3 text-xs">
              <div className="flex items-center justify-between">
                <dt className="text-[#A8B6C9]">Total Selected / Tracked</dt>
                <dd className="font-semibold tabular-nums text-[#F1F5F9]">{stats.total}</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-[#A8B6C9]">Uploaded &amp; Validated</dt>
                <dd className="font-semibold tabular-nums text-[#5EE0AD]">{stats.readyCount}</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-[#A8B6C9]">Failed Uploads</dt>
                <dd className="font-semibold tabular-nums text-[#FF9CA9]">{stats.failedCount}</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-[#A8B6C9]">Total Footage Duration</dt>
                <dd className="font-semibold tabular-nums text-[#F1F5F9]">
                  {formatDuration(stats.totalDuration)}
                </dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-[#A8B6C9]">Stored Volume Size</dt>
                <dd className="font-semibold tabular-nums text-[#F1F5F9]">
                  {formatBytes(stats.totalBytes)}
                </dd>
              </div>
              <div className="flex items-center justify-between border-t border-[#33465A] pt-3">
                <dt className="text-[#A8B6C9]">Storage Readiness</dt>
                <dd className="font-medium text-[#5EE0CA]">
                  {stats.readyCount > 0 ? "Private Volume Ready" : "Awaiting Uploads"}
                </dd>
              </div>
            </dl>
          </div>

          <div className="rounded-xl border border-[#33465A] bg-[#142133] p-6 space-y-4">
            <h2 className="border-b border-[#33465A] pb-3 text-base font-semibold text-[#F1F5F9]">
              D. Investigation Submission
            </h2>
            <p className="text-xs leading-relaxed text-[#A8B6C9]">
              Your draft investigation and validated CCTV recordings are durably saved in your
              private officer workspace. Parallel AI agent orchestration and GPU processing
              connect in Phase 5.
            </p>
            <button
              type="button"
              disabled
              title="Parallel AI orchestration connects in Phase 5"
              className="w-full cursor-not-allowed rounded-lg border border-[#33465A] bg-[#0B111C] px-4 py-2.5 text-xs font-semibold text-[#A8B6C9] opacity-75"
            >
              Start Investigation (Phase 5)
            </button>
          </div>
        </aside>
      </div>

      {previewClip && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Video preview"
        >
          <div className="w-full max-w-3xl rounded-xl border border-[#33465A] bg-[#142133] p-4 shadow-2xl">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="truncate text-sm font-semibold text-[#F1F5F9]">{previewClip.title}</h3>
              <button
                type="button"
                onClick={() => {
                  URL.revokeObjectURL(previewClip.url);
                  setPreviewClip(null);
                }}
                className="rounded-lg border border-[#33465A] px-2.5 py-1 text-xs text-[#A8B6C9] hover:text-[#F1F5F9]"
              >
                Close
              </button>
            </div>
            <video src={previewClip.url} controls autoPlay className="max-h-[70vh] w-full rounded-lg bg-black" />
          </div>
        </div>
      )}
    </div>
  );
}
