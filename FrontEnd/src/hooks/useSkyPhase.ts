import { useEffect, useState } from "react";

export type SkyPhase = "day" | "dusk" | "night";

function computeSkyPhase(date = new Date()): SkyPhase {
  const h = date.getHours() + date.getMinutes() / 60;
  if (h >= 8 && h < 18) return "day";
  if ((h >= 6 && h < 8) || (h >= 18 && h < 21)) return "dusk";
  return "night";
}

export function useSkyPhase(): SkyPhase {
  const [phase, setPhase] = useState<SkyPhase>(() => computeSkyPhase());

  useEffect(() => {
    const id = window.setInterval(() => setPhase(computeSkyPhase()), 60000);
    return () => window.clearInterval(id);
  }, []);

  return phase;
}
