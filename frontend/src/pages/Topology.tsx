import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Network } from "lucide-react";
import { api } from "../api/client";
import type { Component, Dependency, HealthStatus } from "../api/types";
import { PageHeader } from "../components/Layout";
import { Card, EmptyState, ErrorAlert, Loading, StatusBadge, SyntheticBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { humanize, pct } from "../lib/format";
import { useAction } from "../lib/hooks";
import { layerLayout } from "../lib/layout";
import { useShell } from "../lib/shell";

type TopoNode = Node<{ comp: Component; dim: boolean; focus: boolean }, "component">;

function ComponentNode({ data }: NodeProps<TopoNode>) {
  const c = data.comp;
  const status = (c.health?.effective_status ?? "unknown") as HealthStatus;
  return (
    <div className={`topo-node ${status} ${data.dim ? "dim" : ""} ${data.focus ? "focus" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <div className="name">{c.name}</div>
      <div className="meta">
        <StatusBadge status={status} />
      </div>
      <div className="meta">
        {humanize(c.kind)} · {c.criticality}
        {c.availability_24h !== null && <span className="num"> · {pct(c.availability_24h, 2)}</span>}
      </div>
      {c.is_synthetic && <div style={{ marginTop: 4 }}><SyntheticBadge /></div>}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { component: ComponentNode };

function reach(start: number, adj: Map<number, number[]>): Set<number> {
  const seen = new Set<number>();
  const stack = [...(adj.get(start) ?? [])];
  while (stack.length) {
    const n = stack.pop()!;
    if (seen.has(n)) continue;
    seen.add(n);
    stack.push(...(adj.get(n) ?? []));
  }
  return seen;
}

export function TopologyPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const focus = Number(params.get("focus")) || null;
  const [onlyUnhealthy, setOnlyUnhealthy] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["topology"],
    queryFn: () => api.get<{ nodes: Component[]; edges: Dependency[] }>("/topology"),
    refetchInterval: 15000,
  });
  const connect = useAction(
    (c: { source: number; target: number }) => api.post("/dependencies", { source_id: c.source, target_id: c.target }),
    ["topology", "components"],
  );
  const removeEdge = useAction((id: number) => api.del(`/dependencies/${id}`), ["topology", "components"]);

  const byId = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n])), [data]);
  const { upstream, downstream } = useMemo(() => {
    const deps = new Map<number, number[]>();
    const dependents = new Map<number, number[]>();
    for (const e of data?.edges ?? []) {
      deps.set(e.source_id, [...(deps.get(e.source_id) ?? []), e.target_id]);
      dependents.set(e.target_id, [...(dependents.get(e.target_id) ?? []), e.source_id]);
    }
    return focus
      ? { upstream: reach(focus, deps), downstream: reach(focus, dependents) }
      : { upstream: new Set<number>(), downstream: new Set<number>() };
  }, [data, focus]);

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as TopoNode[], edges: [] as Edge[] };
    let visible = data.nodes;
    if (onlyUnhealthy) {
      const bad = new Set(
        data.nodes.filter((n) => ["down", "degraded", "impacted"].includes(n.health?.effective_status ?? "")).map((n) => n.id),
      );
      data.nodes.forEach((n) => n.health?.impacted_by.forEach((id) => bad.add(id)));
      visible = data.nodes.filter((n) => bad.has(n.id));
    }
    const ids = visible.map((n) => n.id);
    const idSet = new Set(ids);
    const visEdges = data.edges.filter((e) => idSet.has(e.source_id) && idSet.has(e.target_id));
    const pos = layerLayout(ids, visEdges.map((e) => ({ source: e.source_id, target: e.target_id })));
    const related = (id: number) => !focus || id === focus || upstream.has(id) || downstream.has(id);
    const nodes: TopoNode[] = visible.map((c) => ({
      id: String(c.id),
      type: "component",
      position: pos.get(c.id) ?? { x: 0, y: 0 },
      data: { comp: c, dim: !related(c.id), focus: c.id === focus },
    }));
    const edges: Edge[] = visEdges.map((e) => {
      const target = byId.get(e.target_id);
      const failing = target?.health?.effective_status === "down" || target?.health?.effective_status === "impacted";
      const onPath = !focus || (related(e.source_id) && related(e.target_id));
      return {
        id: String(e.id),
        source: String(e.source_id),
        target: String(e.target_id),
        animated: failing,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        style: {
          stroke: failing ? "var(--st-down)" : "var(--border-strong)",
          strokeWidth: failing ? 2 : 1.5,
          opacity: onPath ? 1 : 0.15,
        },
        label: e.description || undefined,
      };
    });
    return { nodes, edges };
  }, [data, onlyUnhealthy, focus, upstream, downstream, byId]);

  const onConnect = useCallback(
    (c: Connection) => {
      if (!can("operator") || !c.source || !c.target) return;
      connect.mutate({ source: Number(c.source), target: Number(c.target) });
    },
    [can, connect],
  );

  const focused = focus ? byId.get(focus) : undefined;

  return (
    <>
      <PageHeader
        title="Infrastructure topology"
        subtitle="Arrows point from a component to what it depends on. Click a node to see its blast radius."
        setOpen={setOpen}
      />
      <div className="content">
        <div className="row between">
          <div className="legend" aria-label="Legend">
            {(["up", "degraded", "impacted", "down", "unmonitored"] as HealthStatus[]).map((s) => <StatusBadge key={s} status={s} />)}
            <span className="muted">Animated red edge: dependency is failing</span>
          </div>
          <div className="row">
            <label className="check">
              <input type="checkbox" checked={onlyUnhealthy} onChange={(e) => setOnlyUnhealthy(e.target.checked)} />
              Only unhealthy paths
            </label>
            {focus && <button className="btn sm" onClick={() => setParams({})}>Clear focus</button>}
          </div>
        </div>
        <ErrorAlert error={connect.error ?? removeEdge.error ?? error} />
        <div className={focused ? "grid side" : ""}>
          <Card flush>
            {isLoading ? <Loading /> : !data?.nodes.length ? (
              <EmptyState icon={<Network size={30} />} title="No components to map"
                action={<Link className="btn primary" to="/services">Add components</Link>}>
                Add services and infrastructure components, then declare dependencies to build the topology.
              </EmptyState>
            ) : nodes.length === 0 ? (
              <EmptyState icon={<Network size={30} />} title="Everything is healthy">No unhealthy paths to show.</EmptyState>
            ) : (
              <div className="topology">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  fitView
                  minZoom={0.2}
                  nodesConnectable={can("operator")}
                  onConnect={onConnect}
                  onNodeClick={(_, n) => setParams(Number(n.id) === focus ? {} : { focus: n.id })}
                  onPaneClick={() => focus && setParams({})}
                  onEdgesDelete={(es) => {
                    if (!can("operator")) return;
                    es.forEach((e) => removeEdge.mutate(Number(e.id)));
                  }}
                  deleteKeyCode={can("operator") ? ["Delete", "Backspace"] : null}
                  proOptions={{ hideAttribution: true }}
                >
                  <Background gap={20} color="var(--grid)" />
                  <Controls showInteractive={false} />
                  <MiniMap pannable zoomable nodeColor={(n) => {
                    const s = (n.data as { comp: Component }).comp.health?.effective_status;
                    return s === "down" ? "#d03b3b" : s === "impacted" ? "#ec835a" : s === "degraded" ? "#fab219" : "#8a8a85";
                  }} />
                </ReactFlow>
              </div>
            )}
          </Card>
          {focused && (
            <Card title={focused.name} actions={<Link className="small" to={`/services?id=${focused.id}`}>Details</Link>}>
              <div className="stack">
                <StatusBadge status={focused.health?.effective_status ?? "unknown"} />
                {focused.health?.impacted_by.length ? (
                  <div className="alert warn small">
                    Probably impacted by failing dependency: {focused.health.impacted_by.map((id) => byId.get(id)?.name).join(", ")}
                  </div>
                ) : null}
                <h3 style={{ marginTop: 8 }}>Depends on ({upstream.size})</h3>
                <div className="small">{[...upstream].map((id) => byId.get(id)?.name).join(", ") || "—"}</div>
                <h3 style={{ marginTop: 8 }}>Blast radius ({downstream.size})</h3>
                <div className="small">{[...downstream].map((id) => byId.get(id)?.name).join(", ") || "Nothing depends on this component."}</div>
                {can("operator") && (
                  <p className="muted small" style={{ marginTop: 12 }}>
                    Tip: drag from a node's bottom handle to another node to declare a dependency; select an edge and press Delete to remove it.
                  </p>
                )}
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
