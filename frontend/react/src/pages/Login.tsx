import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";
import { isSupabaseConfigured, sanitizeLocalRedirect, supabase } from "../lib/supabase";

export function Login() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [forgotMode, setForgotMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setNotice("");
    if (!supabase) {
      setError("Supabase authentication is not configured for this build.");
      return;
    }
    if (!email.trim() || !password) {
      setError("Enter both your email address and password.");
      return;
    }
    setBusy(true);
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (signInError) {
        setError("Invalid email or password, or email address not yet verified.");
        return;
      }
      const next = sanitizeLocalRedirect(searchParams.get("next"), "/");
      navigate(next, { replace: true });
    } catch {
      setError("Unable to reach authentication service. Please retry.");
    } finally {
      setBusy(false);
    }
  };

  const handleResetRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setNotice("");
    if (!supabase) {
      setError("Supabase authentication is not configured.");
      return;
    }
    if (!email.trim()) {
      setError("Enter the email address associated with your account.");
      return;
    }
    setBusy(true);
    try {
      await supabase.auth.resetPasswordForEmail(email.trim(), {
        redirectTo: `${window.location.origin}/reset-password`,
      });
      // Non-enumerating generic confirmation
      setNotice("If an account exists for that email address, a password reset link has been sent.");
    } catch {
      setNotice("If an account exists for that email address, a password reset link has been sent.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0B111C] px-4 py-10 text-[#F1F5F9]">
      <div className="w-full max-w-md rounded-xl border border-[#33465A] bg-[#142133] p-6 sm:p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#5EE0CA]/40 bg-[#5EE0CA]/10 text-[#5EE0CA]">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-[#F1F5F9]">CCTV AI Agent</h1>
            <p className="text-xs text-[#A8B6C9]">
              {forgotMode ? "Reset account password" : "Authorized Law Enforcement Sign In"}
            </p>
          </div>
        </div>

        {!isSupabaseConfigured && (
          <div role="alert" className="mb-4 rounded-lg border border-amber-500/40 bg-amber-950/40 p-3 text-xs text-[#F7BF62]">
            Missing <code>VITE_SUPABASE_URL</code> or <code>VITE_SUPABASE_PUBLISHABLE_KEY</code>.
          </div>
        )}

        {error && (
          <div role="alert" className="mb-4 rounded-lg border border-rose-500/40 bg-rose-950/40 p-3 text-xs text-[#FF9CA9]">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="mb-4 rounded-lg border border-emerald-500/40 bg-emerald-950/40 p-3 text-xs text-[#5EE0AD]">
            {notice}
          </div>
        )}

        {forgotMode ? (
          <form onSubmit={handleResetRequest} className="space-y-4">
            <label className="block text-xs font-medium text-[#A8B6C9]">
              Official Email Address
              <div className="relative mt-1.5">
                <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[#A8B6C9]" />
                <input
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="officer@department.gov"
                  className="w-full rounded-lg border border-[#33465A] bg-[#0B111C] py-2 pl-9 pr-3 text-sm text-[#F1F5F9]"
                />
              </div>
            </label>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-[#5EE0CA] py-2.5 text-sm font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
            >
              {busy ? "Sending..." : "Send Reset Instructions"}
            </button>
            <button
              type="button"
              onClick={() => {
                setForgotMode(false);
                setError("");
                setNotice("");
              }}
              className="w-full rounded-lg border border-[#33465A] py-2 text-xs font-medium text-[#A8B6C9] hover:bg-[#1B2A3D] hover:text-[#F1F5F9]"
            >
              Back to Sign In
            </button>
          </form>
        ) : (
          <form onSubmit={handleSignIn} className="space-y-4">
            <label className="block text-xs font-medium text-[#A8B6C9]">
              Official Email Address
              <div className="relative mt-1.5">
                <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[#A8B6C9]" />
                <input
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="officer@department.gov"
                  className="w-full rounded-lg border border-[#33465A] bg-[#0B111C] py-2 pl-9 pr-3 text-sm text-[#F1F5F9]"
                />
              </div>
            </label>

            <label className="block text-xs font-medium text-[#A8B6C9]">
              Password
              <div className="relative mt-1.5">
                <Lock className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[#A8B6C9]" />
                <input
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  className="w-full rounded-lg border border-[#33465A] bg-[#0B111C] py-2 pl-9 pr-10 text-sm text-[#F1F5F9]"
                />
                <button
                  type="button"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-2 top-1.5 rounded p-1 text-[#A8B6C9] hover:text-[#F1F5F9]"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </label>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => {
                  setForgotMode(true);
                  setError("");
                  setNotice("");
                }}
                className="text-xs font-medium text-[#5EE0CA] hover:underline"
              >
                Forgot password?
              </button>
            </div>

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-[#5EE0CA] py-2.5 text-sm font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
            >
              {busy ? "Signing in..." : "Sign In"}
            </button>
          </form>
        )}

        <div className="mt-6 border-t border-[#33465A] pt-4 text-center text-xs text-[#A8B6C9]">
          New police officer?{" "}
          <Link to="/register" className="font-medium text-[#5EE0CA] hover:underline">
            Register for Officer Verification
          </Link>
        </div>
      </div>
    </div>
  );
}
