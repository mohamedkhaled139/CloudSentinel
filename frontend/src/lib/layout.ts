/**
 * Layered layout for a dependency DAG: consumers on top, foundational dependencies at the bottom.
 * Each node's layer is the length of the longest path from any top-level consumer to it.
 */
export interface LayoutEdge {
  source: number;
  target: number;
}

export function layerLayout(
  ids: number[],
  edges: LayoutEdge[],
  opts: { xGap?: number; yGap?: number } = {},
): Map<number, { x: number; y: number; layer: number }> {
  const xGap = opts.xGap ?? 230;
  const yGap = opts.yGap ?? 120;
  const deps = new Map<number, number[]>();
  const indeg = new Map<number, number>(ids.map((id) => [id, 0]));
  for (const e of edges) {
    if (!indeg.has(e.source) || !indeg.has(e.target)) continue;
    deps.set(e.source, [...(deps.get(e.source) ?? []), e.target]);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  // Kahn's algorithm, assigning layer = max(parent layer + 1).
  const layer = new Map<number, number>(ids.map((id) => [id, 0]));
  const queue = ids.filter((id) => (indeg.get(id) ?? 0) === 0);
  const remaining = new Map(indeg);
  while (queue.length) {
    const n = queue.shift()!;
    for (const t of deps.get(n) ?? []) {
      layer.set(t, Math.max(layer.get(t) ?? 0, (layer.get(n) ?? 0) + 1));
      remaining.set(t, (remaining.get(t) ?? 1) - 1);
      if (remaining.get(t) === 0) queue.push(t);
    }
  }
  const byLayer = new Map<number, number[]>();
  for (const id of ids) byLayer.set(layer.get(id)!, [...(byLayer.get(layer.get(id)!) ?? []), id]);

  // Order nodes within a layer by the average position of their parents (barycentre heuristic).
  const xIndex = new Map<number, number>();
  const parents = new Map<number, number[]>();
  for (const e of edges) parents.set(e.target, [...(parents.get(e.target) ?? []), e.source]);
  const layers = [...byLayer.keys()].sort((a, b) => a - b);
  const out = new Map<number, { x: number; y: number; layer: number }>();
  const widest = Math.max(...[...byLayer.values()].map((l) => l.length), 1);
  for (const l of layers) {
    const nodes = byLayer.get(l)!;
    nodes.sort((a, b) => {
      const avg = (id: number) => {
        const ps = (parents.get(id) ?? []).map((p) => xIndex.get(p)).filter((x): x is number => x !== undefined);
        return ps.length ? ps.reduce((s, x) => s + x, 0) / ps.length : Number.MAX_SAFE_INTEGER;
      };
      return avg(a) - avg(b) || a - b;
    });
    const offset = ((widest - nodes.length) * xGap) / 2;
    nodes.forEach((id, i) => {
      xIndex.set(id, i + (widest - nodes.length) / 2);
      out.set(id, { x: offset + i * xGap, y: l * yGap, layer: l });
    });
  }
  return out;
}
