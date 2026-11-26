import { useEffect, useState } from "react";
import { Capacitor } from "@capacitor/core";
import { Network } from "@capacitor/network";

export function useNetworkStatus(): boolean {
  const [connected, setConnected] = useState<boolean>(() =>
    typeof navigator !== "undefined" ? navigator.onLine : true
  );

  useEffect(() => {
    const onOnline = () => setConnected(true);
    const onOffline = () => setConnected(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);

    let removeListener: (() => void) | null = null;
    if (Capacitor.isNativePlatform()) {
      void Network.getStatus().then((st) => setConnected(st.connected)).catch(() => {});
      void Network.addListener("networkStatusChange", (st) => {
        setConnected(st.connected);
      }).then((handle) => {
        removeListener = () => void handle.remove();
      }).catch(() => {});
    }

    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      removeListener?.();
    };
  }, []);

  return connected;
}
