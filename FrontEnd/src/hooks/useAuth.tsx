import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  getStoredToken,
  registerUnauthorizedHandler,
  setStoredToken,
} from "../api/client";
import { api } from "../api/endpoints";
import { md5 } from "../utils/md5";

interface AuthContextValue {
  token: string | null;
  isAuthenticated: boolean;
  login: (username: string, passwordPlain: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(getStoredToken);

  const logout = useCallback(() => {
    setStoredToken(null);
    setToken(null);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    registerUnauthorizedHandler(logout);
  }, [logout]);

  const login = useCallback(async (username: string, passwordPlain: string) => {
    const res = await api.login({
      username: username.trim(),
      password: md5(passwordPlain),
    });
    setStoredToken(res.token);
    setToken(res.token);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        token,
        isAuthenticated: Boolean(token),
        login,
        logout,
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
