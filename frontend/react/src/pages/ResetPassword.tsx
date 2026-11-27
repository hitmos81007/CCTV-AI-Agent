import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";

export function ResetPassword() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!supabase) {
      setError("Authentication provider is not configured.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters long.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const { error: updateErr } = await supabase.auth.updateUser({ password });
      if (updateErr) {
        setError("Unable to update password. Request a fresh reset link from Sign In.");
        return;
      }
      setDone(true);
      setTimeout(() => navigate("/login", { replace: true }), 1500);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0B111C] px-4 text-[#F1F5F9]">
      <div className="w-full max-w-md rounded-xl border border-[#33465A] bg-[#142133] p-6 sm:p-8">
        <h1 className="text-xl font-semibold text-[#F1F5F9]">Set New Password</h1>
        <p className="mt-1 text-xs text-[#A8B6C9]">Choose a strong password for your officer account.</p>

        {error && (
          <div role="alert" className="mt-4 rounded-lg border border-rose-500/40 bg-rose-950/40 p-3 text-xs text-[#FF9CA9]">
            {error}
          </div>
        )}
        {done ? (
          <div className="mt-4 rounded-lg border border-emerald-500/40 bg-emerald-950/40 p-4 text-xs text-[#5EE0AD]">
            Password updated. Redirecting to Sign In...
          </div>
        ) : (
          <form onSubmit={handleUpdate} className="mt-5 space-y-4">
            <label className="block text-xs font-medium text-[#A8B6C9]">
              New Password
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>
            <label className="block text-xs font-medium text-[#A8B6C9]">
              Confirm New Password
              <input
                type="password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-[#5EE0CA] py-2.5 text-sm font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
            >
              {busy ? "Updating..." : "Update Password"}
            </button>
          </form>
        )}

        <div className="mt-5 text-center text-xs">
          <Link to="/login" className="text-[#5EE0CA] hover:underline">
            Back to Sign In
          </Link>
        </div>
      </div>
    </div>
  );
}
