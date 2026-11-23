import { describe, expect, it } from "vitest";
import { layerLayout } from "../lib/layout";

describe("layerLayout", () => {
  it("places dependencies below their dependents", () => {
    // 1 -> 2 -> 3, and 1 -> 3
    const pos = layerLayout([1, 2, 3], [
      { source: 1, target: 2 },
      { source: 2, target: 3 },
      { source: 1, target: 3 },
    ]);
    expect(pos.get(1)!.layer).toBe(0);
    expect(pos.get(2)!.layer).toBe(1);
    expect(pos.get(3)!.layer).toBe(2); // longest path wins
    expect(pos.get(3)!.y).toBeGreaterThan(pos.get(2)!.y);
  });

  it("handles isolated nodes and spreads siblings horizontally", () => {
    const pos = layerLayout([1, 2, 3, 4], [
      { source: 1, target: 2 },
      { source: 1, target: 3 },
    ]);
    expect(pos.size).toBe(4);
    expect(pos.get(2)!.layer).toBe(1);
    expect(pos.get(2)!.x).not.toBe(pos.get(3)!.x);
    expect(pos.get(4)!.layer).toBe(0);
  });
});
