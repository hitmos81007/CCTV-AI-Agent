import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { fetchAuthMe } from "../api";
import { isSupabaseConfigured, supabase } from "../lib/supabase";
import type { AuthMeResponse } from "../types";

interface AuthContextValue {
  session: Session | null;
  authLoading: boolean;
  me: AuthMeResponse | null;
  meLoading: boolean;
  meError: Error | null;
  refreshMe: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState<boolean>(isSupabaseConfigured);

  useEffect(() => {
    if (!supabase) {
      setAuthLoading(false);
      return;
    }

    let mounted = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return;
      setSession(data.session ?? null);
      setAuthLoading(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((event, nextSession) => {
      if (!mounted) return;
      setSession((prev) => {
        if (prev?.user?.id !== nextSession?.user?.id || event === "SIGNED_OUT") {
          queryClient.clear();
        }
        return nextSession ?? null;
      });
      setAuthLoading(false);
    });

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, [queryClient]);

  const userId = session?.user?.id ?? null;

  const {
    data: me = null,
    isLoading: meLoading,
    error: meQueryError,
    refetch,
  } = useQuery({
    queryKey: ["auth-me", userId],
    queryFn: fetchAuthMe,
    enabled: Boolean(userId),
    retry: false,
  });

  // Subscribe to sanitized verification_events for live status / admin queue updates
  useEffect(() => {
    if (!supabase || !userId) return;
    const client = supabase;
    const channel = client
      .channel(`verification-events-${userId}`)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "verification_events" },
        () => {
          queryClient.invalidateQueries({ queryKey: ["auth-me", userId] });
          queryClient.invalidateQueries({ queryKey: ["admin"] });
        }
      )
      .subscribe();

    return () => {
      client.removeChannel(channel);
    };
  }, [userId, queryClient]);

  const refreshMe = useCallback(async () => {
    if (!userId) return;
    await refetch();
  }, [userId, refetch]);

  const signOut = useCallback(async () => {
    if (supabase) {
      await supabase.removeAllChannels();
      await supabase.auth.signOut();
    }
    setSession(null);
    queryClient.clear();
  }, [queryClient]);

  return (
    <AuthContext.Provider
      value={{
        session,
        authLoading,
        me,
        meLoading: Boolean(userId) && meLoading,
        meError: (meQueryError as Error | null) ?? null,
        refreshMe,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
