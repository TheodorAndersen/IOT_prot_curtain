import { useQuery } from "@tanstack/react-query";
import { api } from "../api/endpoints";
import { useAuth } from "./useAuth";

export function useStatusPolling(fastPollActive = false) {
  const { isAuthenticated } = useAuth();

  return useQuery({
    queryKey: ["status"],
    queryFn: () => api.getStatus(),
    enabled: isAuthenticated,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (fastPollActive || data?.moving) return 1500;
      return 5000;
    },
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: 1,
  });
}
