import React from "react";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { PageHeader, StatusChip } from "../components/Ui";
import { formatDistrictName } from "../lib/districts";

export function Account() {
  const { me, signOut } = useAuth();
  const profile = me?.profile;

  return (
    <div>
      <PageHeader
        title="Account Information"
        subtitle="Active role authorization and identity details"
      />

      <div className="max-w-xl space-y-5">
        <div className="rounded-xl border border-[#33465A] bg-[#142133] p-6 space-y-4 text-sm">
          <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
            <span className="text-xs text-[#A8B6C9]">Email</span>
            <span className="font-medium text-[#F1F5F9]">{me?.email ?? "—"}</span>
          </div>

          {me?.is_district_admin ? (
            <>
              <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                <span className="text-xs text-[#A8B6C9]">Administrator Authorization</span>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/40 bg-emerald-950/60 px-2.5 py-0.5 text-xs font-medium text-[#5EE0AD]">
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Active District Administrator</span>
                </span>
              </div>
              <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                <span className="text-xs text-[#A8B6C9]">Assigned Admin District</span>
                <span className="font-medium text-[#5EE0CA]">
                  {formatDistrictName(me.admin_district_id)}
                </span>
              </div>
              <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                <span className="text-xs text-[#A8B6C9]">Officer Investigation Access</span>
                <span className="text-xs text-[#A8B6C9]">
                  {me.is_approved_officer ? "Approved" : "Not Granted (Admin Role Separation)"}
                </span>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                <span className="text-xs text-[#A8B6C9]">Officer Verification Status</span>
                <StatusChip status={profile?.verification_status ?? "draft"} />
              </div>

              {profile && (
                <>
                  <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                    <span className="text-xs text-[#A8B6C9]">Full Name</span>
                    <span>{profile.full_name}</span>
                  </div>
                  <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                    <span className="text-xs text-[#A8B6C9]">Designation &amp; Station</span>
                    <span className="text-right text-xs">
                      {profile.designation} • {profile.police_station}
                    </span>
                  </div>
                  <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
                    <span className="text-xs text-[#A8B6C9]">District</span>
                    <span>{formatDistrictName(profile.approved_district_id || profile.claimed_district_id)}</span>
                  </div>
                </>
              )}
            </>
          )}

          <div className="pt-2">
            <button
              type="button"
              onClick={signOut}
              className="rounded-lg border border-[#33465A] bg-[#0B111C] px-4 py-2 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D]"
            >
              Sign Out
            </button>
          </div>
        </div>

        {me?.is_district_admin && profile && (
          <div className="rounded-xl border border-[#33465A] bg-[#142133]/70 p-5 text-xs text-[#A8B6C9] space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold uppercase tracking-wider text-[#A8B6C9]">
                Separate Officer Application Record
              </span>
              <StatusChip status={profile.verification_status} />
            </div>
            <p className="leading-relaxed">
              This account also has an unapproved officer onboarding record for{" "}
              <strong className="text-[#F1F5F9]">{formatDistrictName(profile.claimed_district_id)}</strong>.
              District Administrator privileges remain active independently and cannot be used to
              self-approve officer investigation access.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
