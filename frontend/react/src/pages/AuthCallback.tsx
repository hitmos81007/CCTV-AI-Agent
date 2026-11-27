import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { sanitizeLocalRedirect, supabase } from "../lib/supabase";

export function AuthCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState("");

  useEffect(() => {
    if (!supabase) {
      setError("Authentication provider is not configured.");
      return;
    }
    const errDesc = searchParams.get("error_description") || searchParams.get("error");
    if (errDesc) {
      setError("The verification or recovery link is invalid or expired.");
      return;
    }
    const next = sanitizeLocalRedirect(searchParams.get("next"), "/");
    supabase.auth.getSession().then(({ data, error: sessionErr }) => {
      if (sessionErr || !data.session) {
        navigate("/login", { replace: true });
        return;
      }
      navigate(next, { replace: true });
    });
  }, [navigate, searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0B111C] px-4 text-[#F1F5F9]">
      <div className="w-full max-w-md rounded-xl border border-[#33465A] bg-[#142133] p-6 text-center">
        {error ? (
          <>
            <h1 className="text-lg font-semibold text-[#FF9CA9]">Verification Link Issue</h1>
            <p className="mt-2 text-sm text-[#A8B6C9]">{error}</p>
            <Link
              to="/login"
              className="mt-4 inline-block rounded-lg bg-[#5EE0CA] px-4 py-2 text-xs font-semibold text-[#082D2D]"
            >
              Return to Sign In
            </Link>
          </>
        ) : (
          <p className="text-sm text-[#A8B6C9]">Completing authentication...</p>
        )}
      </div>
    </div>
  );
}
