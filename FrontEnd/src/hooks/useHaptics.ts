import { useCallback, useEffect, useRef } from "react";
import { Capacitor } from "@capacitor/core";
import { Haptics, ImpactStyle } from "@capacitor/haptics";

export function useHaptics() {
  const iosSwitchRef = useRef<HTMLLabelElement | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const isIOS =
      /iP(hone|ad|od)/.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (!isIOS || Capacitor.isNativePlatform()) return;

    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.setAttribute("switch", "");
    input.tabIndex = -1;
    label.setAttribute("aria-hidden", "true");
    label.style.display = "none";
    label.append(input);
    document.body.append(label);
    iosSwitchRef.current = label;
    return () => {
      label.remove();
      iosSwitchRef.current = null;
    };
  }, []);

  return useCallback(() => {
    if (Capacitor.isNativePlatform()) {
      void Haptics.impact({ style: ImpactStyle.Light }).catch(() => {});
      return;
    }
    if (typeof navigator !== "undefined" && "vibrate" in navigator) {
      navigator.vibrate(8);
      return;
    }
    iosSwitchRef.current?.click();
  }, []);
}
