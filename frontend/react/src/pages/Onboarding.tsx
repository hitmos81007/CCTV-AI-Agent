import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileText, LogOut, ShieldCheck, Trash2, Upload } from "lucide-react";
import {
  deleteVerificationDocument,
  fetchDistricts,
  fetchVerificationDocumentBlob,
  saveOfficerProfile,
  submitVerificationApplication,
  uploadVerificationDocument,
} from "../api";
import { useAuth } from "../auth/AuthProvider";
import { StatusChip } from "../components/Ui";
import type { VerificationDocType } from "../types";

const MAX_BYTES = 5 * 1024 * 1024;
const ALLOWED_MIMES = new Set(["application/pdf", "image/jpeg", "image/png"]);

export function Onboarding() {
  const { me, refreshMe, signOut } = useAuth();
  const navigate = useNavigate();

  const profile = me?.profile ?? null;
  const [fullName, setFullName] = useState(profile?.full_name ?? "");
  const [designation, setDesignation] = useState(profile?.designation ?? "");
  const [policeStation, setPoliceStation] = useState(profile?.police_station ?? "");
  const [districtId, setDistrictId] = useState(profile?.claimed_district_id ?? "");
  const [employeeRef, setEmployeeRef] = useState(profile?.employee_ref ?? "");
  const [profileSaved, setProfileSaved] = useState(Boolean(profile));

  const [docType, setDocType] = useState<VerificationDocType>("redacted_id");
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewMime, setPreviewMime] = useState<string>("");

  const { data: districtData } = useQuery({
    queryKey: ["districts"],
    queryFn: fetchDistricts,
  });
  const districts = districtData?.items ?? [];

  useEffect(() => {
    if (profile) {
      setFullName(profile.full_name);
      setDesignation(profile.designation);
      setPoliceStation(profile.police_station);
      setDistrictId(profile.claimed_district_id);
      setEmployeeRef(profile.employee_ref ?? "");
      setProfileSaved(true);
    } else if (districts.length > 0 && !districtId) {
      setDistrictId(districts[0].id);
    }
  }, [profile, districts, districtId]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const currentVersion = profile?.submission_version ?? 1;
  const activeDocs = (me?.documents ?? []).filter((d) => d.submission_version === currentVersion);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await saveOfficerProfile({
        full_name: fullName.trim(),
        designation: designation.trim(),
        police_station: policeStation.trim(),
        claimed_district_id: districtId,
        employee_ref: employeeRef.trim() || null,
      });
      await refreshMe();
      setProfileSaved(true);
      setNotice("Step 2 professional details saved. Now upload 1–3 supporting documents below.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save profile.");
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = async (file: File | undefined) => {
    if (!file) return;
    setError("");
    setNotice("");
    if (!profileSaved) {
      setError("Save your Step 2 professional details before uploading documents.");
      return;
    }
    if (!ALLOWED_MIMES.has(file.type)) {
      setError("Only PDF, JPEG, or PNG documents are permitted.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("File exceeds the 5 MB per-document size limit.");
      return;
    }
    if (activeDocs.length >= 3) {
      setError("Maximum of 3 documents allowed per submission.");
      return;
    }
    setBusy(true);
    setUploadPct(0);
    try {
      await uploadVerificationDocument(docType, file, (pct) => setUploadPct(pct));
      await refreshMe();
      setNotice(`Uploaded ${file.name} securely.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  };

  const handleDeleteDoc = async (docId: string) => {
    setError("");
    setBusy(true);
    try {
      await deleteVerificationDocument(docId);
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        setPreviewUrl(null);
      }
      await refreshMe();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove document.");
    } finally {
      setBusy(false);
    }
  };

  const handlePreview = async (docId: string, mime: string) => {
    setError("");
    try {
      const blob = await fetchVerificationDocumentBlob(docId);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(URL.createObjectURL(blob));
      setPreviewMime(mime);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed.");
    }
  };

  const handleSubmitApplication = async () => {
    setError("");
    setBusy(true);
    try {
      await submitVerificationApplication();
      await refreshMe();
      navigate("/verification-status", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0B111C] px-4 py-8 text-[#F1F5F9]">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-4 border-b border-[#33465A] pb-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 text-[#5EE0CA]">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-xl font-semibold">Police Officer Verification</h1>
              <p className="text-xs text-[#A8B6C9]">
                Steps 2 &amp; 3 • Professional Details &amp; Supporting Credentials
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {profile && <StatusChip status={profile.verification_status} />}
            <button
              type="button"
              onClick={signOut}
              className="flex items-center gap-1.5 rounded-lg border border-[#33465A] bg-[#142133] px-3 py-1.5 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
            >
              <LogOut className="h-3.5 w-3.5" />
              <span>Sign Out</span>
            </button>
          </div>
        </div>

        {profile?.verification_status === "additional_info_required" && profile.status_reason && (
          <div className="mb-5 rounded-xl border border-amber-500/40 bg-amber-950/30 p-4 text-sm text-[#F7BF62]">
            <div className="flex items-center gap-2 font-semibold">
              <AlertTriangle className="h-4 w-4" />
              <span>Additional Information Requested by District Administrator</span>
            </div>
            <p className="mt-1 text-xs text-[#F1F5F9]">{profile.status_reason}</p>
          </div>
        )}

        {error && (
          <div role="alert" className="mb-5 rounded-xl border border-rose-500/40 bg-rose-950/40 p-3.5 text-xs text-[#FF9CA9]">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="mb-5 rounded-xl border border-emerald-500/40 bg-emerald-950/40 p-3.5 text-xs text-[#5EE0AD]">
            {notice}
          </div>
        )}

        {/* Step 2: Professional Details */}
        <section className="mb-6 rounded-xl border border-[#33465A] bg-[#142133] p-6">
          <h2 className="text-base font-semibold text-[#F1F5F9]">Step 2 — Professional Information</h2>
          <p className="mt-1 text-xs text-[#A8B6C9]">
            Submitted details remain unverified claims until reviewed by an authorized District Administrator.
          </p>

          <form onSubmit={handleSaveProfile} className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="block text-xs font-medium text-[#A8B6C9]">
              Full Name
              <input
                type="text"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>

            <label className="block text-xs font-medium text-[#A8B6C9]">
              Designation / Rank
              <input
                type="text"
                required
                value={designation}
                onChange={(e) => setDesignation(e.target.value)}
                placeholder="Sub-Inspector / Investigating Officer"
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>

            <label className="block text-xs font-medium text-[#A8B6C9]">
              Police Station / Department
              <input
                type="text"
                required
                value={policeStation}
                onChange={(e) => setPoliceStation(e.target.value)}
                placeholder="Central Cyber & Traffic Unit"
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>

            <label className="block text-xs font-medium text-[#A8B6C9]">
              Assigned District
              <select
                required
                value={districtId}
                onChange={(e) => setDistrictId(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              >
                {districts.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-xs font-medium text-[#A8B6C9] sm:col-span-2">
              Official Employee / Badge Reference (optional)
              <input
                type="text"
                value={employeeRef}
                onChange={(e) => setEmployeeRef(e.target.value)}
                placeholder="Synthetic ref e.g. BADGE-SYNTH-104"
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>

            <div className="sm:col-span-2">
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-[#5EE0CA] px-5 py-2.5 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
              >
                {busy ? "Saving..." : "Save Professional Details"}
              </button>
            </div>
          </form>
        </section>

        {/* Step 3: Supporting Document Upload */}
        <section className="rounded-xl border border-[#33465A] bg-[#142133] p-6">
          <h2 className="text-base font-semibold text-[#F1F5F9]">Step 3 — Supporting Credential Upload</h2>
          <p className="mt-1 text-xs text-[#A8B6C9]">
            Upload 1–3 supporting documents (PDF, JPEG, PNG; max 5 MB each). For prototype testing, use
            synthetic or redacted documents only. Do not upload Aadhaar, PAN, home addresses, or biometrics.
          </p>

          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="block flex-1 text-xs font-medium text-[#A8B6C9]">
              Document Type
              <select
                value={docType}
                onChange={(e) => setDocType(e.target.value as VerificationDocType)}
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              >
                <option value="redacted_id">Redacted Police-Issued Identification</option>
                <option value="authorization_letter">Departmental Authorization Letter</option>
                <option value="posting_confirmation">Appointment / Posting Confirmation</option>
              </select>
            </label>

            <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-[#5EE0CA]/50 bg-[#5EE0CA]/10 px-4 py-2 text-xs font-semibold text-[#5EE0CA] hover:bg-[#5EE0CA]/20">
              <Upload className="h-4 w-4" />
              <span>Choose File (PDF/JPEG/PNG)</span>
              <input
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
                disabled={busy || !profileSaved}
                onChange={(e) => {
                  handleFileUpload(e.target.files?.[0]);
                  e.target.value = "";
                }}
                className="sr-only"
              />
            </label>
          </div>

          {uploadPct !== null && (
            <div className="mt-3">
              <div className="mb-1 text-xs text-[#A8B6C9]">Uploading... {uploadPct}%</div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-[#0B111C]">
                <div className="h-full bg-[#5EE0CA]" style={{ width: `${uploadPct}%` }} />
              </div>
            </div>
          )}

          <div className="mt-5 space-y-2.5">
            {activeDocs.length === 0 ? (
              <p className="rounded-lg border border-dashed border-[#33465A] p-4 text-center text-xs text-[#A8B6C9]">
                No supporting documents uploaded for this submission yet.
              </p>
            ) : (
              activeDocs.map((doc) => (
                <div
                  key={doc.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#33465A] bg-[#0B111C] p-3"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <FileText className="h-4 w-4 shrink-0 text-[#5EE0CA]" />
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-[#F1F5F9]">
                        {doc.original_filename}
                      </div>
                      <div className="text-[11px] text-[#A8B6C9]">
                        {doc.document_type.replace(/_/g, " ")} • {(doc.byte_size / 1024).toFixed(1)} KB
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handlePreview(doc.id, doc.mime_type)}
                      className="rounded border border-[#33465A] px-2.5 py-1 text-xs text-[#F1F5F9] hover:bg-[#1B2A3D]"
                    >
                      Preview
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove ${doc.original_filename}`}
                      onClick={() => handleDeleteDoc(doc.id)}
                      className="rounded border border-rose-500/40 p-1.5 text-[#FF9CA9] hover:bg-rose-950/50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>

          {previewUrl && (
            <div className="mt-4 rounded-lg border border-[#33465A] bg-[#0B111C] p-3">
              <div className="mb-2 flex items-center justify-between text-xs text-[#A8B6C9]">
                <span>Document Preview</span>
                <button
                  type="button"
                  onClick={() => {
                    URL.revokeObjectURL(previewUrl);
                    setPreviewUrl(null);
                  }}
                  className="text-[#5EE0CA] hover:underline"
                >
                  Close Preview
                </button>
              </div>
              {previewMime === "application/pdf" ? (
                <iframe title="Credential PDF Preview" src={previewUrl} className="h-72 w-full rounded" />
              ) : (
                <img src={previewUrl} alt="Uploaded verification credential preview" className="max-h-72 rounded object-contain" />
              )}
            </div>
          )}

          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-[#33465A] pt-5">
            <span className="text-xs text-[#A8B6C9]">
              Submitting locks edits until the District Administrator completes review.
            </span>
            <button
              type="button"
              disabled={busy || !profileSaved || activeDocs.length < 1}
              onClick={handleSubmitApplication}
              className="rounded-lg bg-[#5EE0CA] px-5 py-2.5 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
            >
              Submit Application for Administrative Review
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
