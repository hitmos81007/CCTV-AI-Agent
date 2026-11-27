import { supabase } from "./lib/supabase";
import type {
  AdminApplicationDetail,
  AdminApplicationSummary,
  AdminDecisionType,
  AdminOverview,
  AuthMeResponse,
  CaseSummary,
  CaseVideo,
  District,
  OfficerProfile,
  Progress,
  Sighting,
  VerificationDocType,
  VerificationDocument,
  VerificationReview,
} from "./types";

export const apiBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getAccessToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(apiBase + path, {
      ...options,
      headers,
    });
  } catch {
    throw new Error("Cannot reach the backend API. Verify the local server is running.");
  }

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message =
      typeof payload.detail === "string"
        ? payload.detail
        : `Request failed with status ${response.status}.`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function fetchDistricts(): Promise<{ items: District[] }> {
  return apiRequest("/api/v1/districts");
}

export function fetchAuthMe(): Promise<AuthMeResponse> {
  return apiRequest("/api/v1/auth/me");
}

export function saveOfficerProfile(payload: {
  full_name: string;
  designation: string;
  police_station: string;
  claimed_district_id: string;
  employee_ref?: string | null;
}): Promise<{ profile: OfficerProfile }> {
  return apiRequest("/api/v1/verification/profile", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function uploadVerificationDocument(
  documentType: VerificationDocType,
  file: File,
  onProgress?: (pct: number) => void
): Promise<{ document: VerificationDocument }> {
  const token = await getAccessToken();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiBase}/api/v1/verification/documents`);
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }
    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable && onProgress) {
        onProgress(Math.round((evt.loaded / evt.total) * 100));
      }
    };
    xhr.onload = () => {
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(xhr.responseText || "{}");
      } catch {
        body = {};
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as { document: VerificationDocument });
      } else {
        reject(new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during document upload."));
    const formData = new FormData();
    formData.append("document_type", documentType);
    formData.append("file", file);
    xhr.send(formData);
  });
}

export function deleteVerificationDocument(docId: string): Promise<{ deleted: boolean; id: string }> {
  return apiRequest(`/api/v1/verification/documents/${encodeURIComponent(docId)}`, {
    method: "DELETE",
  });
}

export function submitVerificationApplication(): Promise<{ profile: OfficerProfile }> {
  return apiRequest("/api/v1/verification/submit", { method: "POST" });
}

export async function fetchVerificationDocumentBlob(docId: string): Promise<Blob> {
  const token = await getAccessToken();
  const response = await fetch(`${apiBase}/api/v1/verification/documents/${encodeURIComponent(docId)}/content`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new Error(`Document preview unavailable (${response.status})`);
  }
  return response.blob();
}

export function fetchAdminOverview(): Promise<AdminOverview> {
  return apiRequest("/api/v1/admin/overview");
}

export function fetchAdminApplications(status?: string): Promise<{ district_id: string; items: AdminApplicationSummary[] }> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest(`/api/v1/admin/applications${qs}`);
}

export function fetchAdminApplicationDetail(userId: string): Promise<AdminApplicationDetail> {
  return apiRequest(`/api/v1/admin/applications/${encodeURIComponent(userId)}`);
}

export function submitAdminDecision(
  userId: string,
  payload: { decision: AdminDecisionType; reason?: string; request_key: string }
): Promise<{ profile: OfficerProfile; review: VerificationReview; idempotent: boolean }> {
  return apiRequest(`/api/v1/admin/applications/${encodeURIComponent(userId)}/decision`, {
    method: "POST",
    headers: { "Idempotency-Key": payload.request_key },
    body: JSON.stringify(payload),
  });
}

export function fetchAdminHistory(): Promise<{ district_id: string; items: VerificationReview[] }> {
  return apiRequest("/api/v1/admin/history");
}

export function fetchCases(): Promise<CaseSummary[]> {
  return apiRequest("/api/v1/cases");
}

export function createCase(
  payload: {
    case_id?: string;
    title?: string;
    description: string;
    clue_notes?: string | null;
    incident_time_text?: string | null;
    authorized: boolean;
    initial_zone?: string;
    neighbors?: Record<string, string[]>;
  },
  idempotencyKey?: string
): Promise<CaseSummary> {
  return apiRequest("/api/v1/cases", {
    method: "POST",
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    body: JSON.stringify(payload),
  });
}

export function updateCaseDraft(
  caseId: string,
  payload: {
    title?: string;
    description?: string;
    clue_notes?: string | null;
    incident_time_text?: string | null;
  }
): Promise<CaseSummary> {
  return apiRequest(`/api/v1/cases/${encodeURIComponent(caseId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function uploadCaseVideo(
  caseId: string,
  file: File,
  options: {
    uploadKey: string;
    sourceLabel?: string;
    recordedAt?: string | null;
    onProgress?: (pct: number) => void;
    signal?: AbortSignal;
  }
): Promise<CaseVideo> {
  return new Promise((resolve, reject) => {
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token) {
          reject(new Error("Session expired. Please sign in again."));
          return;
        }
        const form = new FormData();
        form.append("case_id", caseId);
        form.append("upload_key", options.uploadKey);
        if (options.sourceLabel?.trim()) form.append("source_label", options.sourceLabel.trim());
        if (options.recordedAt?.trim()) form.append("recorded_at", options.recordedAt.trim());
        form.append("file", file);

        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${apiBase}/api/v1/videos`);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.setRequestHeader("Idempotency-Key", options.uploadKey);
        if (options.signal) {
          options.signal.addEventListener("abort", () => {
            xhr.abort();
          });
        }
        xhr.upload.onprogress = (ev) => {
          if (ev.lengthComputable && options.onProgress) {
            options.onProgress(Math.round((ev.loaded / ev.total) * 100));
          }
        };
        xhr.onabort = () => reject(new Error("Upload cancelled."));
        xhr.onload = () => {
          let parsed: Record<string, unknown> = {};
          try {
            parsed = JSON.parse(xhr.responseText || "{}");
          } catch {
            // ignore
          }
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(parsed as unknown as CaseVideo);
          } else {
            const msg = typeof parsed.detail === "string" ? parsed.detail : `Video upload failed (${xhr.status})`;
            reject(new Error(msg));
          }
        };
        xhr.onerror = () => reject(new Error("Network error while uploading video."));
        xhr.send(form);
      } catch (err) {
        reject(err);
      }
    })();
  });
}

export function deleteDraftVideo(videoSourceId: string): Promise<{ deleted: boolean; video_source_id: string }> {
  return apiRequest(`/api/v1/videos/${encodeURIComponent(videoSourceId)}`, { method: "DELETE" });
}

export async function fetchVideoPlaybackBlob(videoSourceId: string): Promise<Blob> {
  const token = await getAccessToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(`${apiBase}/api/v1/videos/${encodeURIComponent(videoSourceId)}/file`, { headers });
  if (!resp.ok) {
    throw new Error(`Unable to load video playback stream (${resp.status}).`);
  }
  return resp.blob();
}

export function fetchCase(caseId: string): Promise<CaseSummary> {
  return apiRequest(`/api/v1/cases/${encodeURIComponent(caseId)}`);
}

export function fetchCaseProgress(caseId: string): Promise<Progress> {
  return apiRequest(`/api/v1/cases/${encodeURIComponent(caseId)}/progress`);
}

export function fetchSightings(caseId: string): Promise<Sighting[]> {
  return apiRequest(`/api/v1/sightings?case_id=${encodeURIComponent(caseId)}`);
}
