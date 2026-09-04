import { useEffect, useMemo, useState } from "react";
import { auth } from "@/api/client";
import { motion } from "framer-motion";
import { Activity, ArrowUpRight, BarChart3, Bot, LineChart, Sparkles, Wallet } from "lucide-react";
import { getRuns, type RunRow } from "@/api/client";
import { faNum, fmtCls, fmtPct } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Badge, StatusDot } from "@/components/ui/Badge";
import { Card, CardSkeleton, EmptyState, StatCard } from "@/components/ui/primitives";

/* ---------------------------------- data ---------------------------------- */

function useRuns() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getRuns().then(setRuns).catch((e) => setError(e.message));
  }, []);
  return { runs, error };
}

/* --------------------------------- pieces --------------------------------- */

function Logo() {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand to-brand-soft shadow-[0_8px_20px_-6px_rgba(99,102,241,.6)]">
        <BarChart3 size={20} className="text-white" />
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-extrabold tracking-tight">Vibe Trading</div>
        <div className="text-[10.5px] text-muted">پلتفرم هوشمند بک‌تست</div>
      </div>
    </div>
  );
}

function Hero({ onCta }: { onCta: () => void }) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-line bg-gradient-to-b from-panel2/90 to-panel/60 px-6 py-14 text-center md:px-14 md:py-20">
      <div
        className="pointer-events-none absolute inset-0 opacity-60"
        style={{
          background:
            "radial-gradient(600px 220px at 50% -40px, rgba(99,102,241,.28), transparent 70%)",
        }}
      />
      <motion.div
        initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, ease: [0.2, 0.7, 0.3, 1] }}
        className="relative mx-auto max-w-2xl"
      >
        <div className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border border-brand/25 bg-brand/10 px-4 py-1.5 text-[12px] font-semibold text-indigo-300">
          <Sparkles size={13} /> تیم AI چندعاملی تحلیل بازار
        </div>
        <h1 className="text-[28px] font-extrabold leading-[1.5] tracking-tight md:text-[38px]">
          بک‌تست حرفه‌ای بزن،<br />
          <span className="text-gradient">با اعتماد معامله کن</span>
        </h1>
        <p className="mx-auto mt-4 max-w-lg text-[14px] leading-8 text-muted md:text-[15px]">
          استراتژی‌ات رو به زبان ساده بگو — تیم هوش مصنوعی دیتای واقعی بازار رو تحلیل
          می‌کنه، بک‌تست کامل می‌گیره و گزارش حرفه‌ای PDF تحویل میده.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button size="lg" onClick={onCta}>
            <Bot size={17} /> شروع تحلیل با AI
          </Button>
          <Button size="lg" variant="outline" onClick={() => (location.hash = "#reports")}>
            <LineChart size={17} /> مشاهده گزارش‌ها
          </Button>
        </div>
        <div className="mt-9 flex flex-wrap items-center justify-center gap-x-7 gap-y-2 text-[11.5px] text-muted">
          <span className="inline-flex items-center gap-1.5"><StatusDot status="success" /> دیتای واقعی بازار</span>
          <span className="inline-flex items-center gap-1.5"><StatusDot status="success" /> گزارش PDF استاندارد</span>
          <span className="inline-flex items-center gap-1.5"><StatusDot status="success" /> اتصال مستقیم به موتور</span>
        </div>
      </motion.div>
    </section>
  );
}

function RunRowCard({ run, onOpen }: { run: RunRow; onOpen: () => void }) {
  const hasM = run.total_return != null || run.sharpe != null;
  return (
    <motion.button
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -3 }}
      transition={{ duration: 0.3 }}
      onClick={onOpen}
      className={`group w-full rounded-xl2 border border-line bg-panel/70 p-5 text-right backdrop-blur-xl transition-all duration-300 hover:border-brand/40 hover:shadow-[0_16px_40px_-12px_rgba(0,0,0,.6)] ${hasM ? "" : "opacity-70"}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge tone={hasM ? "bt" : "chat"}>{hasM ? "بک‌تست" : "چت"}</Badge>
          <Badge tone={run.status === "success" ? "success" : run.status === "running" ? "running" : "failed"}>
            {run.status === "success" ? "موفق" : run.status === "running" ? "در حال اجرا" : "ناموفق"}
          </Badge>
        </div>
        <ArrowUpRight
          size={16}
          className="text-muted opacity-0 transition-opacity duration-200 group-hover:opacity-100"
        />
      </div>
      <div className="mt-3.5 line-clamp-2 min-h-[44px] text-[13.5px] font-bold leading-7">
        {run.prompt || "—"}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
        {run.total_return != null && (
          <span className={`font-extrabold ${fmtCls(run.total_return)}`}>
            {fmtPct(run.total_return)} بازده
          </span>
        )}
        {run.sharpe != null && (
          <span className="text-muted">شارپ {run.sharpe.toFixed(2)}</span>
        )}
        {(run.start_date || run.end_date) && (
          <span dir="ltr" className="text-muted/80">{run.start_date} → {run.end_date}</span>
        )}
      </div>
    </motion.button>
  );
}

/* ---------------------------------- page ---------------------------------- */

export default function HomePage({ goChat }: { goChat: () => void }) {
  const { runs, error } = useRuns();
  useEffect(() => {
    if (!auth.token) location.href = "/app/legacy.html";
  }, []);

  const stats = useMemo(() => {
    const list = runs || [];
    const bts = list.filter((r) => r.total_return != null || r.sharpe != null);
    const wins = bts.filter((r) => (r.total_return ?? 0) > 0).length;
    const avg = bts.length ? bts.reduce((a, r) => a + (r.total_return ?? 0), 0) / bts.length : null;
    return { total: list.length, bts: bts.length, wins, avg };
  }, [runs]);

  const top = useMemo(() => (runs || []).slice(0, 6), [runs]);

  return (
    <div className="mx-auto w-full max-w-6xl px-5 pb-16 pt-8 md:px-8">
      <header className="mb-10 flex items-center justify-between">
        <Logo />
        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-2.5 rounded-full border border-line bg-panel/70 py-1.5 pl-1.5 pr-4 sm:flex">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand to-brand-soft text-[12px] font-bold text-white">
              {(auth_username() || "?")[0].toUpperCase()}
            </div>
            <span className="text-[12.5px] font-semibold text-muted">{auth_username()}</span>
          </div>
          <Button variant="ghost" size="sm" onClick={logout}>خروج</Button>
        </div>
      </header>

      <Hero onCta={goChat} />

      {/* stats */}
      <div className="mt-8 grid grid-cols-2 gap-4 md:grid-cols-4">
        {runs == null && !error ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-xl2 border border-line bg-panel/50 p-5">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton mt-3 h-6 w-16" />
            </div>
          ))
        ) : (
          <>
            <StatCard label="کل اجراها" value={faNum(stats.total)} />
            <StatCard label="بک‌تست‌های واقعی" value={faNum(stats.bts)} tone="brand" />
            <StatCard label="استراتژی‌های سودده" value={faNum(stats.wins)} tone="pos" />
            <StatCard
              label="میانگین بازده"
              value={stats.avg == null ? "—" : fmtPct(stats.avg)}
              tone={stats.avg == null ? undefined : stats.avg >= 0 ? "pos" : "neg"}
            />
          </>
        )}
      </div>

      {/* recent runs */}
      <div className="mt-10 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-[17px] font-extrabold tracking-tight">
          <Activity size={18} className="text-brand" /> آخرین اجراها
        </h2>
        <a href="#reports" className="text-[12.5px] font-semibold text-brand transition-opacity hover:opacity-80">
          همه گزارش‌ها ←
        </a>
      </div>

      <div className="mt-4">
        {error ? (
          <EmptyState icon="⚠️" title="خطا در دریافت داده" desc={error} />
        ) : runs == null ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
          </div>
        ) : runs.length === 0 ? (
          <EmptyState
            icon="📊"
            title="هنوز اجرایی ثبت نشده"
            desc="اولین تحلیل خودت رو شروع کن — کافیه به زبان ساده بگی چه بک‌تستی می‌خوای."
            action={<Button onClick={goChat}><Bot size={16} /> شروع با AI</Button>}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {top.map((r) => (
              <RunRowCard key={r.run_id} run={r} onOpen={() => (location.hash = "#reports")} />
            ))}
          </div>
        )}
      </div>

      {/* features */}
      <div className="mt-14 grid gap-4 md:grid-cols-3">
        {[
          { icon: <Bot size={19} />, t: "تیم AI چندعاملی", d: "عامل‌های تحلیل، استراتژی و ریسک هماهنگ کار می‌کنند" },
          { icon: <Wallet size={19} />, t: "دیتای واقعی بازار", d: "بکتست روی داده‌های واقعی — نه دیتای شبیه‌سازی‌شده" },
          { icon: <LineChart size={19} />, t: "گزارش PDF حرفه‌ای", d: "متیرهای کامل، نمودار ارزش پرتفوی و جزئیات معاملات" },
        ].map((f) => (
          <Card key={f.t} className="card-hover p-6">
            <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-brand/12 text-brand-soft">
              {f.icon}
            </div>
            <div className="text-[14.5px] font-bold">{f.t}</div>
            <div className="mt-2 text-[12.5px] leading-7 text-muted">{f.d}</div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function auth_username() { return localStorage.getItem("vt_username") || "کاربر"; }
function logout() {
  localStorage.removeItem("vt_token");
  localStorage.removeItem("vt_username");
  location.reload();
}
