"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiError } from "@/lib/api-client";
import * as authService from "@/services/auth";
import type { User } from "@/types/api";

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setUser(await authService.getCurrentUser());
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        console.error("Unable to restore session", error);
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    authService
      .getCurrentUser()
      .then((currentUser) => {
        if (active) setUser(currentUser);
      })
      .catch((error: unknown) => {
        if (!(error instanceof ApiError) || error.status !== 401) {
          console.error("Unable to restore session", error);
        }
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setUser(await authService.login(username, password));
  }, []);

  const logout = useCallback(async () => {
    try {
      await authService.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
