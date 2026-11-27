import React from "react";
import { Link } from "react-router-dom";
import { LogOut, RefreshCw, ShieldCheck } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { StatusChip } from "../components/Ui";
import { formatDistrictName } from "../lib/districts";

export function VerificationStatus() {
  const { me, refreshMe, signOut } = useAuth();
  const profile = me?.profile;
  const status = profile?.verification_status ?? "draft";
  const canResubmit = status === "draft" || status === "additional_info_required";

  return (
    <div className="min-h-screen bg-[#0B111C] px-4 py-10 text-[#F1F5F9]">
      <div className="mx-auto max-w-2xl rounded-xl border border-[#33465A] bg-[#142133] p-6 sm:p-8">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#33465A] pb-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 text-[#5EE0CA]">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-lg font-semibold">Officer Verification Status</h1>
              <p className="text-xs text-[#A8B6C9]">{me?.email ?? ""}</p>
            </div>
          </div>
          <StatusChip status={status} />
        </div>

        <div className="mt-6 space-y-4 text-sm">
          <div className="grid gap-3 rounded-lg border border-[#33465A] bg-[#0B111C] p-4 sm:grid-cols-2">
            <div>
              <div className="text-xs text-[#A8B6C9]">Applicant</div>
              <div className="mt-0.5 font-medium">{profile?.full_name ?? "Not submitted"}</div>
            </div>
            <div>
              <div className="text-xs text-[#A8B6C9]">Claimed District</div>
              <div className="mt-0.5 font-medium">{formatDistrictName(profile?.claimed_district_id)}</div>
            </div>
            <div>
              <div className="text-xs text-[#A8B6C9]">Designation &amp; Station</div>
              <div className="mt-0.5 text-xs text-[#F1F5F9]">
                {profile ? `${profile.designation} • ${profile.police_station}` : "—"}
              </div>
            </div>
            <div>
              <div className="text-xs text-[#A8B6C9]">Submitted At</div>
              <div className="mt-0.5 text-xs text-[#F1F5F9]">
                {profile?.submitted_at ? new Date(profile.submitted_at).toLocaleString() : "Not yet submitted"}
              </div>
            </div>
          </div>

          {status === "pending_review" && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-950/30 p-4 text-xs text-[#F7BF62]">
              Your application is queued for District Administrator review. Investigation tools remain
              locked until approval is recorded.
            </div>
          )}

          {status === "additional_info_required" && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-950/30 p-4 text-xs text-[#F7BF62]">
              <div className="font-semibold">Additional Information Requested</div>
              <p className="mt-1 text-[#F1F5F9]">{profile?.status_reason ?? "Please update your submission."}</p>
            </div>
          )}

          {status === "rejected" && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-950/40 p-4 text-xs text-[#FF9CA9]">
              <div className="font-semibold">Application Rejected</div>
              <p className="mt-1 text-[#F1F5F9]">{profile?.status_reason ?? "No reason recorded."}</p>
            </div>
          )}

          {status === "suspended" && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-950/40 p-4 text-xs text-[#FF9CA9]">
              <div className="font-semibold">Investigation Access Suspended</div>
              <p className="mt-1 text-[#F1F5F9]">
                {profile?.status_reason ?? "Your access has been suspended by a District Administrator."}
              </p>
            </div>
          )}

          {(me?.reviews?.length ?? 0) > 0 && (
            <div>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#A8B6C9]">
                Review History
              </h2>
              <div className="space-y-2">
                {me?.reviews.map((rev) => (
                  <div key={rev.id} className="rounded-lg border border-[#33465A] bg-[#0B111C] p-3 text-xs">
                    <div className="flex items-center justify-between">
                      <StatusChip status={rev.new_status} />
                      <span className="text-[#A8B6C9]">{new Date(rev.created_at).toLocaleString()}</span>
                    </div>
                    {rev.reason && <p className="mt-2 text-[#F1F5F9]">{rev.reason}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-[#33465A] pt-5">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={refreshMe}
              className="flex items-center gap-1.5 rounded-lg border border-[#33465A] px-3 py-2 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              <span>Refresh Status</span>
            </button>
            <button
              type="button"
              onClick={signOut}
              className="flex items-center gap-1.5 rounded-lg border border-[#33465A] px-3 py-2 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
            >
              <LogOut className="h-3.5 w-3.5" />
              <span>Sign Out</span>
            </button>
          </div>

          {canResubmit && (
            <Link
              to="/onboarding"
              className="rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
            >
              Update &amp; Resubmit Credentials
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
