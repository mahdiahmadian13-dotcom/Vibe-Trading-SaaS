// Fleet dashboard widgets (US10, FR-022) — recharts + hand SVG health map.
// Persian RTL, dark-mode, mobile-friendly. Live series: isAnimationActive=false + ring buffer.
import { useMemo } from "react";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  BarChart, Bar, PieChart, Pie, Cell, Legend,
} from "recharts";

export const faNum = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : new Intl.NumberFormat("fa-IR").format(n);

const AXIS = { fontSize: 10, fill: "#a1a1aa" } as const;
const GRID = "rgba(255,255,255,.06)";

export function LoadChart({ points }: { points: Array<{ t: string; v: number }> }) {
  const data = useMemo(
    () => points.slice(-120).map((p) => ({ t: new Date(p.t).toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }), v: p.v })),
    [points],
  );
  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -14 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" tick={AXIS} interval="preserveStartEnd" minTickGap={40} />
        <YAxis tick={AXIS} allowDecimals={false} />
        <Tooltip contentStyle={{ background: "#18181b", border: "1px solid rgba(255,255,255,.1)", borderRadius: 12, fontSize: 12 }} labelStyle={{ color: "#e4e4e7" }} />
        <Line type="monotone" dataKey="v" name="بار" stroke="#38bdf8" strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function TasksBar({ ok, fail, pending }: { ok: number; fail: number; pending: number }) {
  const data = [
    { name: "موفق", v: ok, fill: "#34d399" },
    { name: "ناموفق", v: fail, fill: "#f87171" },
    { name: "در صف/اجرا", v: pending, fill: "#38bdf8" },
  ];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -14 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="name" tick={AXIS} />
        <YAxis tick={AXIS} allowDecimals={false} />
        <Tooltip contentStyle={{ background: "#18181b", border: "1px solid rgba(255,255,255,.1)", borderRadius: 12, fontSize: 12 }} />
        <Bar dataKey="v" radius={[6, 6, 0, 0]} isAnimationActive={false}>
          {data.map((d) => <Cell key={d.name} fill={d.fill} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const DONUT_COLORS = ["#38bdf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#94a3b8"];

export function DistDonut({ slices }: { slices: Array<{ name: string; value: number }> }) {
  const data = slices.filter((s) => s.value > 0);
  if (data.length === 0) return <p className="py-8 text-center text-xs text-muted">باری برای توزیع نیست</p>;
  return (
    <ResponsiveContainer width="100%" height={180}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={48} outerRadius={72} paddingAngle={3} isAnimationActive={false}>
          {data.map((_, i) => <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />)}
        </Pie>
        <Tooltip contentStyle={{ background: "#18181b", border: "1px solid rgba(255,255,255,.1)", borderRadius: 12, fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function HealthMap({ servers }: { servers: Array<{ id: number; name: string; status: string; load: number; engine_healthy: boolean; capability_warning: boolean }> }) {
  const color = (s: (typeof servers)[number]) =>
    s.status === "online" && s.engine_healthy ? "#34d399" : s.status === "draining" || s.status === "degraded" ? "#fbbf24" : "#f87171";
  if (servers.length === 0) return <p className="py-8 text-center text-xs text-muted">گره‌ای ثبت نشده</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {servers.map((s) => (
        <div key={s.id} className="flex min-w-[120px] flex-1 items-center gap-2 rounded-xl border border-white/10 bg-white/[.03] px-3 py-2">
          <svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="6" fill={color(s)} opacity=".9" /></svg>
          <div className="min-w-0">
            <div className="truncate text-xs font-bold">{s.name}</div>
            <div className="text-[10px] text-muted">بار {faNum(s.load)}{s.capability_warning ? " · ضعیف" : ""}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function FleetCards({ fleet }: { fleet: { load: number; queue: number; workers: number; tasks_running: number } }) {
  const cards = [
    { label: "بار کل", v: fleet.load },
    { label: "در صف", v: fleet.queue },
    { label: "ورکر", v: fleet.workers },
    { label: "در حال اجرا", v: fleet.tasks_running },
  ];
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {cards.map((c) => (
        <div key={c.label} className="rounded-xl border border-white/10 bg-white/[.03] px-3 py-2.5 text-center">
          <div className="text-xl font-black">{faNum(c.v)}</div>
          <div className="mt-0.5 text-[11px] text-muted">{c.label}</div>
        </div>
      ))}
    </div>
  );
}

export function LiveTasks({ tasks }: { tasks: Array<{ task_id: string; type: string; status: string | null; worker: string | null; server: string; elapsed_s: number | null }> }) {
  if (tasks.length === 0) return <p className="py-4 text-center text-xs text-muted">تسک فعالی نیست</p>;
  return (
    <div className="max-h-44 space-y-1.5 overflow-auto text-xs">
      {tasks.slice(0, 15).map((t) => (
        <div key={t.task_id} className="flex items-center gap-2 rounded-lg bg-white/[.03] px-2.5 py-1.5">
          <code dir="ltr" className="font-mono text-[10px] text-muted">{t.task_id}</code>
          <span className="font-bold">{t.type}</span>
          <span className="text-muted">{t.server}</span>
          <span className="mr-auto text-muted">{t.elapsed_s !== null ? `${faNum(t.elapsed_s)} ث` : ""}</span>
        </div>
      ))}
    </div>
  );
}
