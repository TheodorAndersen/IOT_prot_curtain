import React, { useRef, useState } from "react";
import { CaretDown, CaretUp } from "@phosphor-icons/react";
import { useHaptics } from "../hooks/useHaptics";
import { useSkyPhase } from "../hooks/useSkyPhase";
import { useI18n } from "../i18n";
import { closedToOpenPct, openToClosedPct } from "../utils/formatters";

interface CurtainWindowProps {
  shownPosition: number | null;
  actualPosition: number | null;
  showGhost: boolean;
  loading: boolean;
  disabled: boolean;
  onDragLive: (pos: number | null) => void;
  onDragCommit: (pos: number) => void;
}

export function CurtainWindow({
  shownPosition,
  actualPosition,
  showGhost,
  loading,
  disabled,
  onDragLive,
  onDragCommit,
}: CurtainWindowProps) {
  const { t, pct, posText } = useI18n();
  const skyPhase = useSkyPhase();
  const haptic = useHaptics();
  const paneRef = useRef<HTMLDivElement | null>(null);

  const [dragging, setDragging] = useState(false);
  const dragStateRef = useRef<{
    pointerId: number | null;
    startY: number;
    armed: boolean;
    lastPos: number | null;
  }>({
    pointerId: null,
    startY: 0,
    armed: false,
    lastPos: null,
  });

  const posFromClientY = (clientY: number): number => {
    const pane = paneRef.current;
    if (!pane) return shownPosition ?? 0;
    const rect = pane.getBoundingClientRect();
    const roller = 18;
    const rail = 12;
    const usable = Math.max(1, rect.height - roller - rail);
    let p = Math.round(((clientY - rect.top - roller - rail / 2) / usable) * 100);
    p = Math.max(0, Math.min(100, p));
    for (const snap of [0, 50, 100]) {
      if (Math.abs(p - snap) <= 2) p = snap;
    }
    return p;
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || loading || (e.pointerType === "mouse" && e.button !== 0)) return;
    dragStateRef.current = {
      pointerId: e.pointerId,
      startY: e.clientY,
      armed: false,
      lastPos: null,
    };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Ignore pointer capture restrictions in jsdom/older browsers
    }
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = dragStateRef.current;
    if (st.pointerId !== e.pointerId) return;
    if (!st.armed) {
      if (Math.abs(e.clientY - st.startY) < 6) return;
      st.armed = true;
      setDragging(true);
    }
    const p = posFromClientY(e.clientY);
    if (p === st.lastPos) return;
    if (st.lastPos !== null && p % 50 === 0) haptic();
    st.lastPos = p;
    onDragLive(p);
  };

  const finishDrag = (e: React.PointerEvent<HTMLDivElement>, commit: boolean) => {
    const st = dragStateRef.current;
    if (st.pointerId !== e.pointerId) return;
    st.pointerId = null;
    if (!st.armed) return;
    const finalPos = st.lastPos;
    st.armed = false;
    st.lastPos = null;
    setDragging(false);
    onDragLive(null);
    if (commit && finalPos !== null) {
      haptic();
      onDragCommit(finalPos);
    }
  };

  const pVal = shownPosition ?? 0;
  const aVal = actualPosition ?? 0;
  const styleVars = {
    "--p": pVal,
    "--a": aVal,
  } as React.CSSProperties;

  return (
    <div className="window-wrap">
      <div
        className={`window${loading ? " loading" : ""}${dragging ? " dragging" : ""}`}
        style={styleVars}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={(e) => finishDrag(e, true)}
        onPointerCancel={(e) => finishDrag(e, false)}
        aria-hidden="true"
      >
        <div
          ref={paneRef}
          className={`pane${skyPhase === "dusk" ? " dusk" : skyPhase === "night" ? " night" : ""}`}
        >
          <div className="sun" />
          <div className="moon" />
          <div className="stars" />
          <div className="mullion" />
          <div className="mover fabric-mover">
            <div className="fabric">
              <div className="rail" />
            </div>
          </div>
          {showGhost && actualPosition !== null && (
            <div className="mover ghost-mover">
              <div className="ghost-line" />
              <span className="ghost-tag">
                {t("status.now", { a: pct(closedToOpenPct(actualPosition)) })}
              </span>
            </div>
          )}
          <div className="roller" />
        </div>
        <div className="handle-layer">
          <div className="mover handle-mover">
            <div className="handle">
              <CaretUp size={16} weight="bold" aria-hidden="true" />
              <CaretDown size={16} weight="bold" aria-hidden="true" />
            </div>
          </div>
        </div>
      </div>

      <input
        type="range"
        className="window-range"
        min={0}
        max={100}
        step={5}
        value={closedToOpenPct(pVal)}
        disabled={disabled || loading}
        aria-label={t("pos.range")}
        aria-valuetext={shownPosition === null ? t("pos.uncalibrated") : posText(shownPosition)}
        onChange={(e) => onDragCommit(openToClosedPct(Number(e.target.value)))}
      />
    </div>
  );
}
