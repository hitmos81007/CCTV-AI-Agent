import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { supabase } from "../lib/supabase";

export function Register() {
  const { refreshMe } = useAuth();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setNotice("");
    if (!supabase) {
      setError("Supabase authentication is not configured.");
      return;
    }
    if (fullName.trim().length < 2) {
      setError("Enter your full name.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters long.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const { data, error: signUpErr } = await supabase.auth.signUp({
        email: email.trim(),
        password,
        options: {
          data: { full_name: fullName.trim() },
        },
      });
      if (signUpErr) {
        setError(signUpErr.message || "Unable to create account.");
        return;
      }
      if (data.user && data.user.identities && data.user.identities.length === 0) {
        setError("An account with this email may already exist. Try signing in or resetting your password.");
        return;
      }
      if (data.session) {
        await refreshMe();
        navigate("/onboarding", { replace: true });
        return;
      }
      setNotice("Account created. Sign in below to continue to Professional Details and Credential Upload.");
    } catch {
      setError("Network error while creating account. Please retry.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0B111C] px-4 py-10 text-[#F1F5F9]">
      <div className="w-full max-w-lg rounded-xl border border-[#33465A] bg-[#142133] p-6 sm:p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 text-[#5EE0CA]">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-[#F1F5F9]">Officer Registration</h1>
            <p className="text-xs text-[#A8B6C9]">
              Step 1 of 3 • Account Creation
            </p>
          </div>
        </div>

        {/* 3-Step progress indicator */}
        <ol className="mb-6 grid grid-cols-3 gap-2 text-center text-[11px] font-medium">
          <li className="rounded-lg border border-[#5EE0CA]/50 bg-[#5EE0CA]/10 px-2 py-1.5 text-[#5EE0CA]">
            1. Account
          </li>
          <li className="rounded-lg border border-[#33465A] bg-[#0B111C] px-2 py-1.5 text-[#A8B6C9]">
            2. Profile
          </li>
          <li className="rounded-lg border border-[#33465A] bg-[#0B111C] px-2 py-1.5 text-[#A8B6C9]">
            3. Credentials
          </li>
        </ol>

        <form onSubmit={handleSignUp} className="space-y-4">
          {error && (
            <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-950/40 p-3 text-xs text-[#FF9CA9]">
              {error}
            </div>
          )}

          {notice && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-950/30 p-3 text-xs text-[#5EE0AD]">
              <p>{notice}</p>
              <Link to="/login" className="mt-2 inline-block font-semibold text-[#5EE0CA] hover:underline">
                Proceed to Sign In
              </Link>
            </div>
          )}

          <label className="block text-xs font-medium text-[#A8B6C9]">
            Full Name
            <input
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Inspector A. Sharma"
              className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
            />
          </label>

          <label className="block text-xs font-medium text-[#A8B6C9]">
            Official Email Address
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="officer@department.gov"
              className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
            />
          </label>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="block text-xs font-medium text-[#A8B6C9]">
              Password
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Min 8 characters"
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>

            <label className="block text-xs font-medium text-[#A8B6C9]">
              Confirm Password
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-enter password"
                className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
              />
            </label>
          </div>

          <p className="rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-[11px] leading-relaxed text-[#A8B6C9]">
            Account creation alone does not grant investigation access. After signing up, you will
            submit professional details and supporting credentials for District Administrator approval.
          </p>

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-[#5EE0CA] px-4 py-2.5 text-sm font-semibold text-[#082D2D] transition-colors hover:bg-[#78E5DC] disabled:opacity-50"
          >
            {busy ? "Creating Account..." : "Create Account & Continue"}
          </button>
        </form>

        <div className="mt-6 border-t border-[#33465A] pt-4 text-center text-xs text-[#A8B6C9]">
          Already registered?{" "}
          <Link to="/login" className="font-medium text-[#5EE0CA] hover:underline">
            Sign In
          </Link>
        </div>
      </div>
    </div>
  );
}
