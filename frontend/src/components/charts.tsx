import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SeriesPoint } from "../api/types";
import { ms, pct, shortTime } from "../lib/format";

const axisProps = {
  stroke: "var(--axis)",
  tick: { fill: "var(--text-3)", fontSize: 11 },
  tickLine: false,
};

interface TipProps {
  active?: boolean;
  payload?: { value: number | null; payload: SeriesPoint }[];
  fmt: (v: number | null) => string;
  label: string;
}

function Tip({ active, payload, fmt, label }: TipProps) {
  if (!active || !payload?.length) return null;
  const p = payload[0];
  return (
    <div className="chart-tooltip">
      <div className="muted">{new Date(p.payload.t).toLocaleString()}</div>
      <div>
        {label}: <span className="v">{fmt(p.value)}</span>
      </div>
      <div className="muted">{p.payload.checks} checks</div>
    </div>
  );
}

/** Availability over time (single series; the card title names it, so no legend). */
export function AvailabilityChart({ data, target }: { data: SeriesPoint[]; target?: number }) {
  const values = data.map((d) => d.availability).filter((v): v is number => v !== null);
  const min = values.length ? Math.min(...values) : 99;
  const floor = Math.max(0, Math.floor(Math.min(min, target ?? 100) - 0.5));
  return (
    <div className="chart" role="img" aria-label="Availability over time">
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="t" tickFormatter={shortTime} minTickGap={40} {...axisProps} />
          <YAxis domain={[floor, 100]} tickFormatter={(v: number) => `${Number(v.toFixed(2))}%`} width={64} {...axisProps} />
          {target !== undefined && (
            <ReferenceLine y={target} stroke="var(--text-3)" strokeDasharray="4 4" label={{
              value: `target ${target}%`, position: "insideBottomRight", fill: "var(--text-3)", fontSize: 11,
            }} />
          )}
          <Tooltip content={<Tip fmt={(v) => pct(v, 3)} label="Availability" />} cursor={{ stroke: "var(--axis)" }} />
          <Area
            type="monotone"
            dataKey="availability"
            stroke="var(--series)"
            strokeWidth={2}
            fill="var(--series)"
            fillOpacity={0.12}
            connectNulls
            isAnimationActive={false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LatencyChart({ data, threshold }: { data: SeriesPoint[]; threshold?: number | null }) {
  return (
    <div className="chart" role="img" aria-label="Response time over time">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="t" tickFormatter={shortTime} minTickGap={40} {...axisProps} />
          <YAxis tickFormatter={(v: number) => ms(v)} width={64} {...axisProps} />
          {threshold ? (
            <ReferenceLine y={threshold} stroke="var(--st-degraded)" strokeDasharray="4 4" label={{
              value: "degraded threshold", position: "insideTopRight", fill: "var(--text-3)", fontSize: 11,
            }} />
          ) : null}
          <Tooltip content={<Tip fmt={ms} label="Avg response" />} cursor={{ stroke: "var(--axis)" }} />
          <Line
            type="monotone"
            dataKey="latency_ms"
            stroke="var(--series)"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Compact uptime strip: one bar per bucket, worst status in the bucket. */
export function StatusStrip({ series, label }: { series: SeriesPoint[]; label?: string }) {
  return (
    <div>
      <div className="strip" role="img" aria-label={label ?? "Status history"}>
        {series.map((p) => (
          <span
            key={p.t}
            className={p.status ?? "empty"}
            title={`${new Date(p.t).toLocaleString()} — ${p.status ?? "no data"}${
              p.availability !== null ? ` (${pct(p.availability)})` : ""
            }`}
          />
        ))}
      </div>
      {series.length > 0 && (
        <div className="strip-axis">
          <span>{new Date(series[0].t).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}</span>
          <span>now</span>
        </div>
      )}
    </div>
  );
}
