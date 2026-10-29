import React, { createContext, useContext, useEffect, useState, useCallback, ReactNode } from "react";
import { apiFetch, ApiError, setApiCsrfToken, setOnUnauthorizedCallback } from "../api/client";

export type UserRole = "ADMIN" | "RECRUITER" | "REVIEWER";

export interface AuthUser {
  userId: string;
  email: string;
  displayName: string;
  role: UserRole;
  isActive: boolean;
  mustChangePassword: boolean;
  idleExpiresAt?: string;
  absoluteExpiresAt?: string;
}

export type AuthStatus =
  | "RESTORING_SESSION"
  | "UNAUTHENTICATED"
  | "PASSWORD_CHANGE_REQUIRED"
  | "AUTHENTICATED";

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  csrfToken: string | null;
  sessionError: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string, newPasswordConfirmation: string) => Promise<void>;
  refreshSession: () => Promise<void>;
  clearSessionError: () => void;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("RESTORING_SESSION");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const applyAuthState = useCallback((userData: AuthUser, token: string) => {
    setUser(userData);
    setCsrfToken(token);
    setApiCsrfToken(token);

    if (userData.mustChangePassword) {
      setStatus("PASSWORD_CHANGE_REQUIRED");
    } else {
      setStatus("AUTHENTICATED");
    }
  }, []);

  const clearAuthState = useCallback((sessionExpiredReason?: string) => {
    setUser(null);
    setCsrfToken(null);
    setApiCsrfToken(null);
    setStatus("UNAUTHENTICATED");
    if (sessionExpiredReason) {
      setSessionError(sessionExpiredReason);
    }
  }, []);

  // Restore session via GET /api/v1/auth/me on initial mount
  const refreshSession = useCallback(async () => {
    try {
      const data = await apiFetch<any>("/api/v1/auth/me");
      if (data && data.userId && data.csrfToken) {
        applyAuthState(
          {
            userId: data.userId,
            email: data.email,
            displayName: data.displayName,
            role: data.role,
            isActive: data.isActive,
            mustChangePassword: data.mustChangePassword,
            idleExpiresAt: data.idleExpiresAt,
            absoluteExpiresAt: data.absoluteExpiresAt,
          },
          data.csrfToken
        );
      } else {
        clearAuthState();
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Initial visit without session cookie is normal unauthenticated state; do not show session expired error
        clearAuthState();
      } else {
        // Network or server error during restore
        clearAuthState("Unable to connect to the authentication service. Please check your network.");
      }
    }
  }, [applyAuthState, clearAuthState]);

  useEffect(() => {
    setOnUnauthorizedCallback(() => {
      clearAuthState("Your session has expired or is no longer valid. Please sign in again.");
    });

    refreshSession();

    return () => {
      setOnUnauthorizedCallback(null);
    };
  }, [clearAuthState, refreshSession]);

  const login = async (email: string, password: string) => {
    setSessionError(null);
    const data = await apiFetch<any>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });

    if (data?.user && data?.csrfToken) {
      applyAuthState(
        {
          userId: data.user.userId,
          email: data.user.email,
          displayName: data.user.displayName,
          role: data.user.role,
          isActive: data.user.isActive,
          mustChangePassword: data.user.mustChangePassword,
        },
        data.csrfToken
      );
    }
  };

  const logout = async () => {
    try {
      await apiFetch("/api/v1/auth/logout", {
        method: "POST",
      });
    } catch {
      // Even if server session fails or was already revoked, clear local state
    } finally {
      clearAuthState();
    }
  };

  const changePassword = async (
    currentPassword: string,
    newPassword: string,
    newPasswordConfirmation: string
  ) => {
    const data = await apiFetch<any>("/api/v1/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        currentPassword,
        newPassword,
        newPasswordConfirmation,
      }),
    });

    if (data?.user && data?.csrfToken) {
      applyAuthState(
        {
          userId: data.user.userId,
          email: data.user.email,
          displayName: data.user.displayName,
          role: data.user.role,
          isActive: data.user.isActive,
          mustChangePassword: data.user.mustChangePassword,
        },
        data.csrfToken
      );
    }
  };

  const clearSessionError = () => {
    setSessionError(null);
  };

  return (
    <AuthContext.Provider
      value={{
        status,
        user,
        csrfToken,
        sessionError,
        login,
        logout,
        changePassword,
        refreshSession,
        clearSessionError,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

export function useOptionalAuth(): AuthContextValue | null {
  const context = useContext(AuthContext);
  return context || null;
}
