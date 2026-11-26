import { useQuery } from "@tanstack/react-query";
import { api } from "../api/endpoints";
import { useAuth } from "./useAuth";

export type DebugIntervalMs = 2000 | 5000 | false;

export function useDebugPolling(intervalMs: DebugIntervalMs, limit = 20) {
  const { isAuthenticated } = useAuth();

  return useQuery({
    queryKey: ["debug", limit],
    queryFn: () => api.getDebug(limit),
    enabled: isAuthenticated,
    refetchInterval: intervalMs,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: 1,
  });
}
