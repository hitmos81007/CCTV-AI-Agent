import React, { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Mail, ShieldCheck } from "lucide-react";
import { supabase } from "../lib/supabase";

export function Register() {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [registeredEmail, setRegisteredEmail] = useState<string | null>(null);
  const [resendMessage, setResendMessage] = useState("");

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
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
          emailRedirectTo: `${window.location.origin}/auth/callback`,
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
      setRegisteredEmail(email.trim());
    } catch {
      setError("Network error while creating account. Please retry.");
    } finally {
      setBusy(false);
    }
  };

  const handleResend = async () => {
    if (!supabase || !registeredEmail) return;
    setResendMessage("");
    setBusy(true);
    try {
      const { error: resendErr } = await supabase.auth.resend({
        type: "signup",
        email: registeredEmail,
        options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
      });
      if (resendErr) {
        setResendMessage("Unable to resend right now (provider rate limits may apply). Wait a minute and retry.");
      } else {
        setResendMessage("Verification email resent. Check your inbox and spam folder.");
      }
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
              Step 1 of 3 • Account Creation &amp; Email Verification
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

        {registeredEmail ? (
          <div className="space-y-4">
            <div className="rounded-xl border border-emerald-500/40 bg-emerald-950/30 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-[#5EE0AD]">
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                <span>Confirm your email address</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-[#A8B6C9]">
                We sent a verification link to <strong className="text-[#F1F5F9]">{registeredEmail}</strong>.
                Click the link in your email, then sign in to complete Step 2 (Professional Details) and
                Step 3 (Supporting Credential Upload).
              </p>
              <p className="mt-2 text-xs text-[#F7BF62]">
                Email verification alone does not grant investigation access; District Administrator approval
                is required after document submission.
              </p>
            </div>

            {resendMessage && (
              <p className="rounded-lg border border-[#33465A] bg-[#0B111C] p-3 text-xs text-[#A8B6C9]">
                {resendMessage}
              </p>
            )}

            <div className="flex flex-col gap-3 sm:flex-row">
              <button
                type="button"
                disabled={busy}
                onClick={handleResend}
                className="flex items-center justify-center gap-2 rounded-lg border border-[#33465A] px-4 py-2.5 text-xs font-medium text-[#F1F5F9] hover:bg-[#1B2A3D] disabled:opacity-50"
              >
                <Mail className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Resend Verification Email</span>
              </button>
              <Link
                to="/login"
                className="flex-1 rounded-lg bg-[#5EE0CA] px-4 py-2.5 text-center text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC]"
              >
                Proceed to Sign In
              </Link>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSignUp} className="space-y-4">
            {error && (
              <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-950/40 p-3 text-xs text-[#FF9CA9]">
                {error}
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

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-xs font-medium text-[#A8B6C9]">
                Password (min 8 chars)
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
                />
              </label>

              <label className="block text-xs font-medium text-[#A8B6C9]">
                Confirm Password
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
                />
              </label>
            </div>

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-[#5EE0CA] py-2.5 text-sm font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
            >
              {busy ? "Creating Account..." : "Create Account & Send Verification"}
            </button>
          </form>
        )}

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
