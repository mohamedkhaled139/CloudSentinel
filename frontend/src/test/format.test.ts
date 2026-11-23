import { describe, expect, it } from "vitest";
import { between, duration, humanize, ms, pct, timeAgo } from "../lib/format";
import { qs } from "../api/client";

describe("format helpers", () => {
  it("formats percentages and missing values", () => {
    expect(pct(99.9512, 2)).toBe("99.95%");
    expect(pct(100)).toBe("100%");
    expect(pct(null)).toBe("—");
  });
  it("formats latency", () => {
    expect(ms(4.21)).toBe("4.2 ms");
    expect(ms(250.4)).toBe("250 ms");
    expect(ms(1530)).toBe("1.53 s");
    expect(ms(null)).toBe("—");
  });
  it("formats durations", () => {
    expect(duration(0.5)).toBe("30s");
    expect(duration(45)).toBe("45m");
    expect(duration(125)).toBe("2h 5m");
    expect(duration(60 * 50)).toBe("2d 2h");
    expect(between("2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z")).toBe("1h 30m");
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(timeAgo("2026-01-01T11:59:30Z", now)).toBe("30s ago");
    expect(timeAgo("2026-01-01T09:00:00Z", now)).toBe("3h ago");
    expect(timeAgo(null, now)).toBe("never");
  });
  it("humanizes identifiers and builds query strings", () => {
    expect(humanize("monitoring_coverage_gap")).toBe("Monitoring coverage gap");
    expect(qs({ a: 1, b: "", c: null, d: "x y" })).toBe("?a=1&d=x+y");
    expect(qs({})).toBe("");
  });
});
