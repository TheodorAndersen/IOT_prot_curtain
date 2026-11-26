import { describe, expect, it } from "vitest";
import {
  closedToOpenPct,
  decodeFlagsByte,
  formatDurationMs,
  formatHexBytes,
  openToClosedPct,
  toBinary8,
} from "../utils/formatters";

describe("formatters", () => {
  it("converts between wire % closed and UI % open", () => {
    expect(closedToOpenPct(0)).toBe(100);
    expect(closedToOpenPct(100)).toBe(0);
    expect(closedToOpenPct(20)).toBe(80);
    expect(openToClosedPct(100)).toBe(0);
    expect(openToClosedPct(0)).toBe(100);
    expect(openToClosedPct(80)).toBe(20);
    expect(closedToOpenPct(-5)).toBe(100);
    expect(openToClosedPct(120)).toBe(0);
  });

  it("decodes 7-bit FPort 1 flags byte accurately", () => {
    expect(decodeFlagsByte(0x24)).toEqual({
      automatic: false,
      moving: false,
      calibrated: true,
      day: false,
      openBySun: false,
      closeBySun: true,
      keepIndoorLight: false,
    });
    expect(toBinary8(0x24)).toBe("00100100");
  });

  it("formats hex bytes and durations", () => {
    expect(formatHexBytes("16032428")).toBe("16 03 24 28");
    expect(formatDurationMs(840)).toBe("840 ms");
    expect(formatDurationMs(12400)).toBe("12.4 s");
  });
});
