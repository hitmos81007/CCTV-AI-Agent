import React from "react";
import { useAuth } from "../auth/AuthProvider";
import { PageHeader, StatusChip } from "../components/Ui";

export function Account() {
  const { me, signOut } = useAuth();
  const profile = me?.profile;

  return (
    <div>
      <PageHeader
        title="Account Information"
        subtitle="Verified identity and authorization status"
      />

      <div className="max-w-xl rounded-xl border border-[#33465A] bg-[#142133] p-6 space-y-4 text-sm">
        <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
          <span className="text-xs text-[#A8B6C9]">Email</span>
          <span className="font-medium text-[#F1F5F9]">{me?.email ?? "—"}</span>
        </div>

        <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
          <span className="text-xs text-[#A8B6C9]">Verification Status</span>
          <StatusChip status={profile?.verification_status ?? (me?.is_district_admin ? "approved" : "draft")} />
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
              <span>{profile.approved_district_id || profile.claimed_district_id}</span>
            </div>
          </>
        )}

        {me?.is_district_admin && (
          <div className="flex items-center justify-between border-b border-[#33465A] pb-3">
            <span className="text-xs text-[#A8B6C9]">Admin District</span>
            <span className="font-medium text-[#5EE0CA]">{me.admin_district_id}</span>
          </div>
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
    </div>
  );
}
