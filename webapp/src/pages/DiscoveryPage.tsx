import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle, CheckCircle2, ChevronDown, FlaskConical, Leaf, RefreshCw,
  Search, Skull, X,
} from "lucide-react";
import {
  DECAY_FA, QUALITY_FA, REGIME_FA, getStrategyEvidence, listStrategies,
  queryStrategies, refreshEvidence,
  type DecayStatus, type EvidenceRow, type Quality, type StrategyItem,
} from "@/api/discovery";
import { getRuns, type RunRow } from "@/api/client";
import { faNum } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { CardSkeleton, EmptyState } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/Badge";

/* --------------------------------- style maps --------------------------------- */

const DECAY_TONE: Record<DecayStatus, "success" | "bt" | "failed"> = {
  fresh: "success",
  aging: "bt",
  stale: "failed",
};
const DECAY_DOT: Record<DecayStatus, string> = {
  fresh: "bg-emerald-400",
  aging: "bg-amber-400",
  stale: "bg-red-400",
};
const QUALITY_TONE: Record<string, "success" | "bt" | "failed"> = {
  adequate: "success",
  marginal: "bt",
  insufficient: "failed",
};

const fmtPct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

/* --------------------------------- verdict --------------------------------- */

function verdict(row: EvidenceRow): { alive: boolean; label: string } {
  if (row.decay_status === "stale") return { alive: false, label: "مرده" };
  if (row.evidence_quality === "insufficient") return { alive: false, label: "مرده" };
  if (row.decay_status === "aging" || row.evidence_quality === "marginal" || row.borderline)
    return { alive: true, label: "لب‌مرزی" };
  return { alive: true, label: "زنده" };
}

/* ------------------------------- evidence card ------------------------------- */

function EvidenceCard({ row }: { row: EvidenceRow }) {
  const [open, setOpen] = useState(false);
  const v = verdict(row);
  return (
    <div className="rounded-xl2 border border-line bg-panel/70 p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 text-right">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${v.alive ? (v.label === "زنده" ? "bg-emerald-400" : "bg-amber-400") : "bg-red-400"}`} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-bold" dir="ltr">{row.strategy_id}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge tone={v.alive ? (v.label === "زنده" ? "success" : "bt") : "failed"}>
              {v.alive ? <Leaf size={11} /> : <Skull size={11} />} {v.label}
            </Badge>
            <Badge tone={DECAY_TONE[row.decay_status]}>{DECAY_FA[row.decay_status]}</Badge>
            <Badge tone={QUALITY_TONE[row.evidence_quality]}>شواهد {QUALITY_FA[row.evidence_quality]}</Badge>
            <span className="text-[11px] text-muted">{REGIME_FA[row.regime] || row.regime}</span>
          </div>
        </div>
        <ChevronDown size={16} className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }} className="overflow-hidden"
          >
            <div className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3 md:grid-cols-4">
              {[
                ["شارپ رژیم", row.sharpe_in_regime != null ? row.sharpe_in_regime.toFixed(2) : "—"],
                ["بازده رژیم", fmtPct(row.return_in_regime)],
                ["مازاد بنچمارک", fmtPct(row.excess_in_regime)],
                ["معاملات", faNum(row.trades_in_regime)],
                ["افت رژیم", fmtPct(row.max_drawdown_in_regime)],
                ["سر‌به‌سری کارمزد", row.breakeven_fee_bps != null ? `${row.breakeven_fee_bps.toFixed(1)} bps` : "—"],
                ["قدمت شواهد", row.evidence_age_days != null ? `${faNum(row.evidence_age_days)} روز` : "—"],
                ["مرحله", row.evidence_stage],
              ].map(([l, val]) => (
                <div key={l} className="rounded-lg border border-line bg-white/[.02] p-2.5">
                  <div className="text-[10.5px] text-muted">{l}</div>
                  <div className="mt-0.5 text-[13px] font-extrabold" dir="ltr">{val}</div>
                </div>
              ))}
            </div>
            {row.date_ranges.length > 0 && (
              <div className="mt-2 text-[11px] text-muted" dir="ltr">
                {row.date_ranges.join(" · ")}
              </div>
            )}
            {row.warnings.length > 0 && (
              <div className="mt-2 space-y-1">
                {row.warnings.map((w, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-[11px] leading-5 text-amber-300/90" dir="ltr">
                    <AlertTriangle size={12} className="mt-1 shrink-0" /> {w}
                  </div>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* -------------------------------- catalog row -------------------------------- */

function CatalogRow({ item, onInspect }: { item: StrategyItem; onInspect: () => void }) {
  return (
    <button
      onClick={onInspect}
      className="w-full rounded-xl2 border border-line bg-panel/70 p-4 text-right transition-all active:border-brand/50 md:hover:-translate-y-0.5 md:hover:border-brand/40"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-bold">{item.name}</div>
          <div className="mt-0.5 truncate text-[10.5px] text-muted/70" dir="ltr">{item.strategy_id}</div>
        </div>
        <Badge tone={item.source === "alpha_zoo" ? "bt" : "chat"}>
          {item.source === "alpha_zoo" ? "Alpha Zoo" : "SDM"}
        </Badge>
      </div>
      {item.description && (
        <div className="mt-2 line-clamp-2 text-[11.5px] leading-6 text-muted" dir="ltr">{item.description}</div>
      )}
      <div className="mt-2.5 flex items-center gap-1.5 text-[11px]">
        {item.has_evidence ? (
          <>
            <CheckCircle2 size={13} className="text-emerald-400" />
            <span className="text-emerald-300">شواهد در {faNum(item.regimes_with_evidence.length)} رژیم</span>
          </>
        ) : (
          <>
            <X size={13} className="text-muted" />
            <span className="text-muted">بدون شواهد محاسباتی</span>
          </>
        )}
      </div>
    </button>
  );
}

/* --------------------------------- main page --------------------------------- */

type Tab = "alive" | "catalog";

export default function DiscoveryPage({ bare = false }: { bare?: boolean }) {
  const [tab, setTab] = useState<Tab>("alive");
  const [items, setItems] = useState<EvidenceRow[] | null>(null);
  const [catalog, setCatalog] = useState<StrategyItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [regime, setRegime] = useState("");
  const [minSharpe, setMinSharpe] = useState("");
  const [includeStale, setIncludeStale] = useState(false);
  const [inspectId, setInspectId] = useState<string | null>(null);
  const [inspectRows, setInspectRows] = useState<EvidenceRow[] | null>(null);
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [refreshForm, setRefreshForm] = useState(false);
  const [refreshSid, setRefreshSid] = useState("");
  const [refreshRid, setRefreshRid] = useState("");
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState("");

  const loadAlive = useCallback(() => {
    setItems(null); setError(""); setNote("");
    queryStrategies({
      regime: regime || undefined,
      min_sharpe: minSharpe ? Number(minSharpe) : undefined,
      min_evidence_quality: "marginal",
      min_trades: 10,
      cost_feasible: false,
      limit: 50,
      include_stale: includeStale,
    })
      .then((r) => { setItems(r.items); if (r.note) setNote(r.note); })
      .catch((e) => setError(e.message));
  }, [regime, minSharpe, includeStale]);

  const loadCatalog = useCallback(() => {
    setCatalog(null); setError("");
    listStrategies(50, 0)
      .then((r) => { setCatalog(r.items); setTotal(r.total); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => { if (tab === "alive") loadAlive(); else loadCatalog(); }, [tab, loadAlive, loadCatalog]);

  useEffect(() => {
    if (refreshForm && runs === null) getRuns().then(setRuns).catch(() => setRuns([]));
  }, [refreshForm, runs]);

  const inspect = (id: string) => {
    setInspectId(id); setInspectRows(null);
    getStrategyEvidence(id).then((r) => setInspectRows(r.rows)).catch(() => setInspectRows([]));
  };

  const aliveCount = useMemo(
    () => (items || []).filter((r) => verdict(r).alive).length, [items]
  );
  const deadCount = useMemo(
    () => (items || []).filter((r) => !verdict(r).alive).length, [items]
  );

  const doRefresh = () => {
    if (!refreshSid.trim() || !refreshRid.trim()) return;
    setRefreshBusy(true); setRefreshMsg("");
    refreshEvidence([{ strategy_id: refreshSid.trim(), run_dir: refreshRid.trim() }])
      .then((r) => {
        setRefreshMsg(`✅ ${faNum(r.rows)} ردیف شواهد برای ${faNum(r.strategies)} استراتژی ساخته شد`);
        loadAlive(); if (tab === "catalog") loadCatalog();
      })
      .catch((e) => setRefreshMsg(`❌ ${e.message}`))
      .finally(() => setRefreshBusy(false));
  };

  return (
    <div className={bare ? "w-full pb-8" : "mx-auto w-full max-w-6xl px-4 pb-24 pt-4 md:px-8 md:pb-16 md:pt-8"}>
      {/* header */}
      <div className="mb-5 flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-[16px] font-extrabold md:text-[18px]">
          <FlaskConical size={19} className="text-brand" /> کشف استراتژی
        </h1>
        <Button variant="outline" size="sm" onClick={() => setRefreshForm(!refreshForm)}>
          <RefreshCw size={14} /> به‌روزرسانی شواهد
        </Button>
      </div>
      <p className="mb-5 text-[12.5px] leading-7 text-muted">
        کدام استراتژی‌ها هنوز زنده‌اند و کدام‌ها مرده‌اند — فقط بر اساس شواهد محاسباتی
        بک‌تست‌های واقعی، نه حدس. استراتژی‌های ضعیف یا قدیمی از توصیه‌ها حذف می‌شن.
      </p>

      {/* refresh panel */}
      <AnimatePresence initial={false}>
        {refreshForm && (
          <motion.div
            initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }} className="overflow-hidden"
          >
            <div className="mb-5 rounded-xl2 border border-brand/25 bg-brand/[.06] p-4">
              <div className="text-[13px] font-bold">ساخت شواهد از بک‌تست‌های خودت</div>
              <div className="mt-1 text-[11.5px] leading-6 text-muted">
                یه شناسه برای استراتژی انتخاب کن و یکی از run_idهای خودت رو بده — موتور
                آرتیفکت‌های واقعی همون ران رو می‌خونه و شواهد رژیم‌به‌رژیم می‌سازه.
              </div>
              <div className="mt-3 grid gap-2.5 md:grid-cols-2">
                <input
                  value={refreshSid} onChange={(e) => setRefreshSid(e.target.value)}
                  placeholder="شناسه استراتژی (مثل: my-btc-macd)"
                  dir="ltr"
                  className="rounded-xl border border-line bg-panel px-3.5 py-2.5 text-left text-[12.5px] outline-none placeholder:text-muted/50 focus:border-brand/50"
                />
                <input
                  value={refreshRid} onChange={(e) => setRefreshRid(e.target.value)}
                  placeholder="run_id (مثل: 20260904_093955_38_eb1948)"
                  dir="ltr" list="discovery-runs"
                  className="rounded-xl border border-line bg-panel px-3.5 py-2.5 text-left font-mono text-[12px] outline-none placeholder:text-muted/50 focus:border-brand/50"
                />
                <datalist id="discovery-runs">
                  {(runs || []).slice(0, 30).map((r) => (
                    <option key={r.run_id} value={r.run_id}>{r.prompt || ""}</option>
                  ))}
                </datalist>
              </div>
              <div className="mt-3 flex items-center gap-2.5">
                <Button size="sm" onClick={doRefresh} disabled={refreshBusy || !refreshSid.trim() || !refreshRid.trim()}>
                  <RefreshCw size={14} /> {refreshBusy ? "در حال ساخت…" : "ساخت شواهد"}
                </Button>
                {refreshMsg && <span className="text-[12px] font-semibold">{refreshMsg}</span>}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* tabs */}
      <div className="mb-4 flex gap-2">
        {([["alive", "زنده‌ها و مرده‌ها"], ["catalog", `کاتالوگ (${faNum(total)})`]] as Array<[Tab, string]>).map(([id, label]) => (
          <button
            key={id} onClick={() => setTab(id)}
            className={`rounded-xl px-4 py-2.5 text-[13px] font-bold transition-colors ${tab === id ? "bg-brand/15 text-indigo-200 ring-1 ring-inset ring-brand/40" : "text-muted hover:bg-white/5 hover:text-ink"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "alive" && (
        <>
          {/* filters */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-xl border border-line bg-panel/70 px-3 py-2">
              <Search size={14} className="text-muted" />
              <select value={regime} onChange={(e) => setRegime(e.target.value)}
                className="bg-transparent text-[12.5px] font-semibold outline-none">
                <option value="">همه رژیم‌ها</option>
                <option value="bull_market">بازار صعودی</option>
                <option value="bear_market">بازار نزولی</option>
                <option value="structural">ساختاری</option>
              </select>
            </div>
            <input
              value={minSharpe} onChange={(e) => setMinSharpe(e.target.value)}
              placeholder="حداقل شارپ" dir="ltr" inputMode="decimal"
              className="w-28 rounded-xl border border-line bg-panel/70 px-3 py-2 text-left text-[12.5px] outline-none placeholder:text-muted/50 focus:border-brand/50"
            />
            <button
              onClick={() => setIncludeStale(!includeStale)}
              className={`rounded-xl border px-3 py-2 text-[12.5px] font-semibold transition-colors ${includeStale ? "border-amber-400/40 bg-amber-400/10 text-amber-200" : "border-line text-muted hover:text-ink"}`}
            >
              {includeStale ? "✅" : ""} شامل کهنه‌ها
            </button>
            <Button size="sm" onClick={loadAlive}>اعمال فیلتر</Button>
          </div>

          {/* summary */}
          {items && items.length > 0 && (
            <div className="mb-4 flex items-center gap-4 text-[12.5px] font-semibold">
              <span className="inline-flex items-center gap-1.5 text-emerald-300">
                <Leaf size={14} /> {faNum(aliveCount)} زنده
              </span>
              <span className="inline-flex items-center gap-1.5 text-red-300">
                <Skull size={14} /> {faNum(deadCount)} مرده
              </span>
            </div>
          )}

          {/* list */}
          {error ? (
            <EmptyState icon="⚠️" title="خطا در دریافت داده" desc={error} />
          ) : items === null ? (
            <div className="grid gap-3 md:grid-cols-2"><CardSkeleton /><CardSkeleton /><CardSkeleton /><CardSkeleton /></div>
          ) : items.length === 0 ? (
            <EmptyState
              icon="🧪"
              title="هنوز شواهدی ثبت نشده"
              desc={note || "جدول شواهد خالیه — از دکمه «به‌روزرسانی شواهد» بالا با یکی از بک‌تست‌های خودت شواهد بساز."}
            />
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {items.map((r) => (
                <EvidenceCard key={`${r.strategy_id}::${r.regime}`} row={r} />
              ))}
            </div>
          )}
        </>
      )}

      {tab === "catalog" && (
        error ? (
          <EmptyState icon="⚠️" title="خطا در دریافت داده" desc={error} />
        ) : catalog === null ? (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3"><CardSkeleton /><CardSkeleton /><CardSkeleton /></div>
        ) : catalog.length === 0 ? (
          <EmptyState icon="📚" title="کاتالوگ خالی" desc="هیچ استراتژی تو موتور پیدا نشد." />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {catalog.map((s) => (
              <CatalogRow key={s.strategy_id} item={s} onInspect={() => inspect(s.strategy_id)} />
            ))}
          </div>
        )
      )}

      {/* inspect modal */}
      <AnimatePresence>
        {inspectId && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-md md:items-center md:p-6"
            onClick={() => setInspectId(null)}
          >
            <motion.div
              initial={{ y: 60, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 60, opacity: 0 }}
              transition={{ type: "spring", stiffness: 320, damping: 32 }}
              onClick={(e) => e.stopPropagation()}
              className="max-h-[92dvh] w-full max-w-2xl overflow-y-auto rounded-t-3xl border border-line bg-panel2 p-5 md:rounded-3xl md:p-7"
            >
              <div className="mb-4 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[14px] font-extrabold" dir="ltr">{inspectId}</div>
                  <div className="mt-0.5 text-[11.5px] text-muted">شواهد رژیم‌به‌رژیم (بدون فیلتر)</div>
                </div>
                <button onClick={() => setInspectId(null)} aria-label="بستن"
                  className="shrink-0 rounded-lg border border-line bg-white/[.03] p-2 text-muted transition-colors hover:text-ink">
                  <X size={16} />
                </button>
              </div>
              {inspectRows === null ? (
                <div className="space-y-3"><div className="skeleton h-16 w-full" /><div className="skeleton h-16 w-full" /></div>
              ) : inspectRows.length === 0 ? (
                <EmptyState icon="🔍" title="شواهدی نیست" desc="برای این استراتژی هیچ ردیف شواهدی ثبت نشده." />
              ) : (
                <div className="space-y-3">
                  {inspectRows.map((r) => <EvidenceCard key={r.regime} row={r} />)}
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
