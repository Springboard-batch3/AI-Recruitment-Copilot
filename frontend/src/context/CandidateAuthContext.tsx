import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  ReactNode,
} from "react";
import {
  CandidateAuthResponse,
  getCandidateMe,
  candidateLogin,
  candidateLogout,
  activateCandidateAccount,
  registerCandidate,
} from "../api/candidateAuth";
import { setApiCsrfToken, ApiError } from "../api/client";

export type CandidateAuthStatus =
  | "RESTORING_SESSION"
  | "UNAUTHENTICATED"
  | "AUTHENTICATED";

export interface CandidateAuthContextValue {
  status: CandidateAuthStatus;
  candidateUser: CandidateAuthResponse | null;
  csrfToken: string | null;
  error: string | null;
  login: (email: string, password: string) => Promise<CandidateAuthResponse>;
  register: (fullName: string, email: string, password: string) => Promise<CandidateAuthResponse>;
  activate: (token: string, password: string, confirmation: string) => Promise<CandidateAuthResponse>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
  clearError: () => void;
}

export const CandidateAuthContext = createContext<
  CandidateAuthContextValue | undefined
>(undefined);

export function CandidateAuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<CandidateAuthStatus>("RESTORING_SESSION");
  const [candidateUser, setCandidateUser] = useState<CandidateAuthResponse | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const applyAuthState = useCallback(
    (userData: CandidateAuthResponse, token: string) => {
      setCandidateUser(userData);
      setCsrfToken(token);
      setApiCsrfToken(token);
      setStatus("AUTHENTICATED");
      setError(null);
    },
    []
  );

  const clearAuthState = useCallback((errorMessage?: string) => {
    setCandidateUser(null);
    setCsrfToken(null);
    setApiCsrfToken(null);
    setStatus("UNAUTHENTICATED");
    if (errorMessage) {
      setError(errorMessage);
    }
  }, []);

  const refreshSession = useCallback(async () => {
    // Skip checking /auth/me if currently visiting the public activation page
    if (typeof window !== "undefined" && window.location.pathname.startsWith("/candidate/activate")) {
      setStatus("UNAUTHENTICATED");
      return;
    }

    try {
      const data = await getCandidateMe();
      if (data && data.userId && data.csrfToken) {
        applyAuthState(data, data.csrfToken);
      } else {
        clearAuthState();
      }
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        clearAuthState();
      } else {
        clearAuthState();
      }
    }
  }, [applyAuthState, clearAuthState]);

  useEffect(() => {
    refreshSession();
  }, [refreshSession]);

  const login = async (email: string, password: string) => {
    setError(null);
    const data = await candidateLogin(email, password);
    if (data && data.userId && data.csrfToken) {
      applyAuthState(data, data.csrfToken);
    }
    return data;
  };

  const register = async (fullName: string, email: string, password: string) => {
    setError(null);
    const data = await registerCandidate(fullName, email, password);
    if (data && data.userId && data.csrfToken) {
      applyAuthState(data, data.csrfToken);
    }
    return data;
  };

  const activate = async (token: string, password: string, confirmation: string): Promise<CandidateAuthResponse> => {
    setError(null);
    const data = await activateCandidateAccount(token, password, confirmation);
    if (data && data.userId && data.csrfToken) {
      applyAuthState(data, data.csrfToken);
    }
    return data;
  };

  const logout = async () => {
    try {
      await candidateLogout();
    } catch {
      // Clear client state even on network error
    } finally {
      clearAuthState();
    }
  };

  const clearError = () => {
    setError(null);
  };

  return (
    <CandidateAuthContext.Provider
      value={{
        status,
        candidateUser,
        csrfToken,
        error,
        login,
        register,
        activate,
        logout,
        refreshSession,
        clearError,
      }}
    >
      {children}
    </CandidateAuthContext.Provider>
  );
}

export function useCandidateAuth(): CandidateAuthContextValue {
  const context = useContext(CandidateAuthContext);
  if (!context) {
    throw new Error("useCandidateAuth must be used within a CandidateAuthProvider");
  }
  return context;
}
