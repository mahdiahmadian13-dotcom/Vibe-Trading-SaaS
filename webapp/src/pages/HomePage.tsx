import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Activity, ArrowUpRight, BarChart3, Bot, LineChart, Menu, Sparkles, Wallet, X } from "lucide-react";
import { auth, getRuns, type RunRow } from "@/api/client";
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

function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand to-brand-soft shadow-[0_8px_20px_-6px_rgba(99,102,241,.6)] md:h-10 md:w-10">
        <BarChart3 size={compact ? 17 : 20} className="text-white" />
      </div>
      <div className="leading-tight">
        <div className="text-[14px] font-extrabold tracking-tight md:text-[15px]">Vibe Trading</div>
        {!compact && <div className="hidden text-[10.5px] text-muted sm:block">پلتفرم هوشمند بک‌تست</div>}
      </div>
    </div>
  );
}

function Hero({ onCta }: { onCta: () => void }) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-line bg-gradient-to-b from-panel2/90 to-panel/60 px-5 py-10 text-center md:px-14 md:py-20">
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
        <div className="mx-auto mb-5 inline-flex items-center gap-2 rounded-full border border-brand/25 bg-brand/10 px-3.5 py-1.5 text-[11px] font-semibold text-indigo-300 md:mb-6 md:text-[12px]">
          <Sparkles size={12} /> تیم AI چندعاملی تحلیل بازار
        </div>
        <h1 className="text-[23px] font-extrabold leading-[1.55] tracking-tight md:text-[38px]">
          بک‌تست حرفه‌ای بزن،<br />
          <span className="text-gradient">با اعتماد معامله کن</span>
        </h1>
        <p className="mx-auto mt-3.5 max-w-lg text-[13px] leading-7 text-muted md:mt-4 md:text-[15px] md:leading-8">
          استراتژی‌ات رو به زبان ساده بگو — تیم هوش مصنوعی دیتای واقعی بازار رو تحلیل
          می‌کنه، بک‌تست کامل می‌گیره و گزارش حرفه‌ای PDF تحویل میده.
        </p>
        <div className="mt-7 flex flex-col items-center justify-center gap-2.5 sm:flex-row md:mt-8 md:gap-3">
          <Button size="lg" className="w-full max-w-xs sm:w-auto" onClick={onCta}>
            <Bot size={17} /> شروع تحلیل با AI
          </Button>
          <Button size="lg" variant="outline" className="w-full max-w-xs sm:w-auto" onClick={() => (location.href = "/app/legacy.html#reports")}>
            <LineChart size={17} /> مشاهده گزارش‌ها
          </Button>
        </div>
        <div className="mt-8 hidden flex-wrap items-center justify-center gap-x-7 gap-y-2 text-[11.5px] text-muted md:flex">
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
      whileTap={{ scale: 0.985 }}
      transition={{ duration: 0.3 }}
      onClick={onOpen}
      className={`group w-full rounded-xl2 border border-line bg-panel/70 p-4 text-right backdrop-blur-xl transition-all duration-300 active:border-brand/50 active:bg-panel2/80 md:hover:-translate-y-1 md:hover:border-brand/40 md:hover:shadow-[0_16px_40px_-12px_rgba(0,0,0,.6)] md:p-5 ${hasM ? "" : "opacity-70"}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge tone={hasM ? "bt" : "chat"}>{hasM ? "بک‌تست" : "چت"}</Badge>
          <Badge tone={run.status === "success" ? "success" : run.status === "running" ? "running" : "failed"}>
            {run.status === "success" ? "موفق" : run.status === "running" ? "در حال اجرا" : "ناموفق"}
          </Badge>
        </div>
        <ArrowUpRight
          size={15}
          className="text-muted opacity-40 transition-opacity md:opacity-0 md:group-hover:opacity-100"
        />
      </div>
      <div className="mt-3 line-clamp-2 min-h-[40px] text-[13px] font-bold leading-6.5 md:min-h-[44px] md:text-[13.5px] md:leading-7">
        {run.prompt || "—"}
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] md:mt-3 md:text-[12px]">
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
  const [menuOpen, setMenuOpen] = useState(false);

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
  const username = localStorage.getItem("vt_username") || "کاربر";

  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-24 pt-4 md:px-8 md:pb-16 md:pt-8">
      {/* ---------- header ---------- */}
      <header className="mb-6 flex items-center justify-between md:mb-10">
        <Logo />
        <div className="flex items-center gap-2.5">
          <div className="hidden items-center gap-2.5 rounded-full border border-line bg-panel/70 py-1.5 pl-1.5 pr-4 sm:flex">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand to-brand-soft text-[12px] font-bold text-white">
              {username[0].toUpperCase()}
            </div>
            <span className="text-[12.5px] font-semibold text-muted">{username}</span>
          </div>
          <button
            className="rounded-lg border border-line bg-white/[.03] p-2.5 text-muted transition-colors hover:text-ink md:hidden"
            onClick={() => setMenuOpen(true)}
            aria-label="منو"
          >
            <Menu size={18} />
          </button>
          <Button variant="ghost" size="sm" className="hidden md:inline-flex" onClick={logout}>خروج</Button>
        </div>
      </header>

      <Hero onCta={goChat} />

      {/* ---------- stats ---------- */}
      <div className="mt-6 grid grid-cols-2 gap-3 md:mt-8 md:grid-cols-4 md:gap-4">
        {runs == null && !error ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-xl2 border border-line bg-panel/50 p-4 md:p-5">
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

      {/* ---------- recent runs ---------- */}
      <div className="mt-9 flex items-center justify-between md:mt-10">
        <h2 className="flex items-center gap-2 text-[15.5px] font-extrabold tracking-tight md:text-[17px]">
          <Activity size={17} className="text-brand" /> آخرین اجراها
        </h2>
        <a href="/app/legacy.html#reports" className="text-[12.5px] font-semibold text-brand transition-opacity hover:opacity-80">
          همه گزارش‌ها ←
        </a>
      </div>

      <div className="mt-4">
        {error ? (
          <EmptyState icon="⚠️" title="خطا در دریافت داده" desc={error} />
        ) : runs == null ? (
          <div className="grid gap-3 md:grid-cols-2 md:gap-4 lg:grid-cols-3">
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
          <div className="grid gap-3 md:grid-cols-2 md:gap-4 lg:grid-cols-3">
            {top.map((r) => (
              <RunRowCard key={r.run_id} run={r} onOpen={() => (location.href = "/app/legacy.html#reports")} />
            ))}
          </div>
        )}
      </div>

      {/* ---------- features (mobile: compact horizontal scroll) ---------- */}
      <div className="mt-12 hidden gap-4 md:grid md:grid-cols-3">
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

      {/* ---------- mobile bottom nav (app-like) ---------- */}
      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/85 backdrop-blur-xl md:hidden" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        <div className="mx-auto grid max-w-md grid-cols-2">
          <NavTab icon={<Bot size={19} />} label="تحلیل با AI" active onClick={goChat} />
          <NavTab icon={<LineChart size={19} />} label="گزارش‌ها" onClick={() => (location.href = "/app/legacy.html#reports")} />
        </div>
      </nav>

      {/* ---------- mobile drawer ---------- */}
      {menuOpen && (
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm md:hidden"
          onClick={() => setMenuOpen(false)}
        >
          <motion.div
            initial={{ x: "-100%" }} animate={{ x: 0 }} exit={{ x: "-100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="absolute left-0 top-0 h-full w-72 border-r border-line bg-panel2 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-6 flex items-center justify-between">
              <Logo compact />
              <button onClick={() => setMenuOpen(false)} className="rounded-lg border border-line bg-white/5 p-2 text-muted" aria-label="بستن">
                <X size={16} />
              </button>
            </div>
            <div className="mb-6 flex items-center gap-3 rounded-xl2 border border-line bg-panel/70 p-3.5">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-brand to-brand-soft text-[14px] font-bold text-white">
                {username[0].toUpperCase()}
              </div>
              <div>
                <div className="text-[13.5px] font-bold">{username}</div>
                <div className="text-[11px] text-muted">حساب فعال</div>
              </div>
            </div>
            <DrawerItem icon={<Bot size={17} />} label="تحلیل با AI" onClick={() => { setMenuOpen(false); goChat(); }} />
            <DrawerItem icon={<LineChart size={17} />} label="گزارش‌های بک‌تست" onClick={() => { setMenuOpen(false); location.href = "/app/legacy.html#reports"; }} />
            <div className="my-4 h-px bg-line" />
            <DrawerItem icon={<X size={17} />} label="خروج از حساب" danger onClick={logout} />
          </motion.div>
        </motion.div>
      )}
    </div>
  );
}

function NavTab({ icon, label, active, onClick }: {
  icon: React.ReactNode; label: string; active?: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`relative flex flex-col items-center gap-1 py-2.5 text-[10.5px] font-semibold transition-colors ${
        active ? "text-indigo-300" : "text-muted"
      }`}
    >
      {active && (
        <motion.span
          layoutId="nav-pill"
          className="absolute -top-px h-0.5 w-12 rounded-full bg-gradient-to-l from-brand to-brand-soft"
        />
      )}
      {icon}
      {label}
    </button>
  );
}

function DrawerItem({ icon, label, onClick, danger }: {
  icon: React.ReactNode; label: string; onClick: () => void; danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-[13.5px] font-semibold transition-colors ${
        danger ? "text-neg/90 hover:bg-neg/10" : "text-muted hover:bg-white/5 hover:text-ink"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function logout() {
  auth.clear();
  location.href = "/app/legacy.html";
}
