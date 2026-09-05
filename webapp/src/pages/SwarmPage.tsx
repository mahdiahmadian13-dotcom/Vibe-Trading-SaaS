import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Check, ChevronLeft, Download, FileText, History, RefreshCw, Users, Zap } from "lucide-react";
import {
  createSwarmRun, downloadSwarmPdf, getSwarmPresets, getSwarmRun, listSwarmRuns, TASK_ICON,
  fuzzySuggest, resolveSymbol, suggestionsFor, varTitle,
  type SwarmPreset, type SwarmRunRow, type SwarmRunStatus, type SwarmVariable,
} from "@/api/swarm";
import { faNum } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { Badge, StatusDot } from "@/components/ui/Badge";

/* =============================== wizard state ============================== */

type Phase = "presets" | "form" | "tracking" | "done" | "runs";

export default function SwarmPage() {
  const [phase, setPhase] = useState<Phase>("presets");
  const [presets, setPresets] = useState<SwarmPreset[] | null>(null);
  const [error, setError] = useState("");
  const [preset, setPreset] = useState<SwarmPreset | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  useEffect(() => {
    getSwarmPresets().then(setPresets).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="mx-auto w-full max-w-4xl px-4 pb-28 pt-5 md:px-8 md:pb-12 md:pt-8">
      <header className="mb-6 md:mb-8">
        <h1 className="flex items-center gap-2 text-[19px] font-extrabold tracking-tight md:text-[22px]">
          <Users size={20} className="text-brand" /> تیم‌های هوش مصنوعی
        </h1>
        <p className="mt-1.5 text-[12.5px] leading-6 text-muted md:text-[13.5px]">
          یک تیم چندعاملی انتخاب کن، متغیرها را مشخص کن و تحلیل تیمی را اجرا کن
        </p>
        {/* tabs: new run / history — mirrors the bot's swarm menu + swhist */}
        <div className="mt-4 flex gap-2">
          <button
            onClick={() => setPhase("presets")}
            className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-[12.5px] font-bold transition-all active:scale-95 ${phase !== "runs" && phase !== "done" ? "bg-gradient-to-l from-brand to-brand-soft text-white shadow-[0_6px_18px_rgba(99,102,241,.4)]" : "border border-line bg-white/[.03] text-muted hover:text-ink"}`}
          >
            <Zap size={14} /> اجرای جدید
          </button>
          <button
            onClick={() => setPhase("runs")}
            className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-[12.5px] font-bold transition-all active:scale-95 ${phase === "runs" || phase === "done" ? "bg-gradient-to-l from-brand to-brand-soft text-white shadow-[0_6px_18px_rgba(99,102,241,.4)]" : "border border-line bg-white/[.03] text-muted hover:text-ink"}`}
          >
            <History size={14} /> تاریخچه اجراها
          </button>
        </div>
      </header>

      {error && <EmptyState icon="⚠️" title="خطا در دریافت پریست‌ها" desc={error} />}

      <AnimatePresence mode="wait">
        {phase === "presets" && (
          <motion.div key="presets" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
            {!presets && !error ? <PresetSkeletons /> : (
              <div className="grid gap-3 sm:grid-cols-2 md:gap-4">
                {(presets || []).map((p) => (
                  <PresetCard key={p.name} preset={p} onClick={() => { setPreset(p); setPhase("form"); }} />
                ))}
              </div>
            )}
          </motion.div>
        )}

        {phase === "form" && preset && (
          <motion.div key="form" initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }}>
            <SwarmForm
              preset={preset}
              onBack={() => setPhase("presets")}
              onLaunched={(id) => { setRunId(id); setPhase("tracking"); }}
            />
          </motion.div>
        )}

        {phase === "tracking" && runId && preset && (
          <motion.div key="tracking" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
            <SwarmTracker runId={runId} presetName={preset.title || preset.name} onDone={() => setPhase("done")} />
          </motion.div>
        )}

        {phase === "done" && (
          <motion.div key="done" initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}>
            <EmptyState
              icon="🎉"
              title="تحلیل تیمی تکمیل شد"
              desc="گزارش کامل تیم در همین صفحه نمایش داده شد. می‌توانی تیم جدیدی اجرا کنی یا گزارش‌های قبلی را ببینی."
              action={
                <div className="flex gap-2">
                  <Button onClick={() => { setPhase("presets"); setPreset(null); }}>تیم جدید</Button>
                  <Button variant="outline" onClick={() => setPhase("runs")}>تاریخچه اجراها</Button>
                </div>
              }
            />
          </motion.div>
        )}

        {phase === "runs" && (
          <motion.div key="runs" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
            <SwarmHistory onOpen={(id) => { setRunId(id); setPhase("tracking"); }} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* =============================== preset cards ============================== */

function PresetCard({ preset, onClick }: { preset: SwarmPreset; onClick: () => void }) {
  return (
    <motion.button
      whileTap={{ scale: 0.985 }}
      onClick={onClick}
      className="group w-full rounded-xl2 border border-line bg-panel/70 p-4 text-right backdrop-blur-xl transition-all duration-300 active:border-brand/50 md:hover:-translate-y-1 md:hover:border-brand/40 md:hover:shadow-[0_16px_40px_-12px_rgba(0,0,0,.6)] md:p-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-extrabold md:text-[14.5px]">{preset.title}</span>
          </div>
          <div dir="ltr" className="mt-0.5 text-right text-[10.5px] text-muted/70">{preset.name}</div>
        </div>
        <ChevronLeft size={16} className="mt-1 shrink-0 text-muted transition-transform group-hover:-translate-x-0.5" />
      </div>
      <p className="mt-2.5 line-clamp-2 text-[12px] leading-6 text-muted">{preset.description}</p>
      <div className="mt-3 flex items-center gap-2">
        <Badge tone="bt"><Users size={11} /> {faNum(preset.agent_count)} ایجنت</Badge>
        <Badge tone="chat">{faNum(preset.variables.length)} متغیر</Badge>
      </div>
    </motion.button>
  );
}

function PresetSkeletons() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 md:gap-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="rounded-xl2 border border-line bg-panel/50 p-5">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="mt-2 h-3 w-24" />
          <Skeleton className="mt-4 h-3 w-full" />
          <Skeleton className="mt-2 h-3 w-3/4" />
        </div>
      ))}
    </div>
  );
}

/* =============================== dynamic form ============================== */

function SwarmForm({ preset, onBack, onLaunched }: {
  preset: SwarmPreset; onBack: () => void; onLaunched: (id: string) => void;
}) {
  const [step, setStep] = useState(0);
  const [values, setValues] = useState<Record<string, string>>({});
  const [freeText, setFreeText] = useState("");
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const vars = preset.variables || [];
  const current: SwarmVariable | undefined = vars[step];
  const suggestions = useMemo(
    () => (current ? suggestionsFor(current.name, preset.name) : []),
    [current, preset.name]
  );
  const fuzzy = useMemo(
    () => (current?.name === "target" ? fuzzySuggest(freeText, suggestionsFor("target", preset.name)) : []),
    [current, freeText, preset.name]
  );

  useEffect(() => { setFreeText(values[current?.name || ""] || ""); }, [step]); // eslint-disable-line

  const pick = (v: string) => {
    if (!current) return;
    const next = { ...values, [current.name]: v };
    setValues(next);
    setFreeText("");
    advance(next);
  };

  const skip = () => advance(values);

  const submitFree = () => {
    if (!current) return;
    let val = freeText.trim();
    if (!val) return;
    if (current.name === "target") {
      val = resolveSymbol(val) || val.toUpperCase();
    }
    const next = { ...values, [current.name]: val };
    setValues(next);
    setFreeText("");
    advance(next);
  };

  function advance(v: Record<string, string>) {
    if (step + 1 < vars.length) setStep(step + 1);
    else launch(v);
  }

  async function launch(final: Record<string, string>) {
    setLaunching(true); setLaunchError("");
    try {
      const res = await createSwarmRun(preset.name, final);
      onLaunched(res.id);
    } catch (e) {
      setLaunchError((e as Error).message || "خطا در اجرای تیم");
      setLaunching(false);
    }
  }

  const crypto = ["crypto_research_lab", "crypto_trading_desk"].includes(preset.name);

  return (
    <div>
      <button onClick={onBack} className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-muted transition-colors hover:text-ink">
        <ArrowRight size={15} /> بازگشت به تیم‌ها
      </button>

      <Card className="p-5 md:p-6">
        {/* progress */}
        <div className="mb-5">
          <div className="mb-2 flex items-center justify-between text-[11.5px] text-muted">
            <span className="font-bold text-ink">{preset.title}</span>
            <span>{faNum(Math.min(step + 1, vars.length))} از {faNum(vars.length)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
            <motion.div
              className="h-full rounded-full bg-gradient-to-l from-brand to-brand-soft"
              animate={{ width: `${(step / Math.max(vars.length, 1)) * 100}%` }}
              transition={{ duration: 0.35 }}
            />
          </div>
        </div>

        {current && (
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 14 }}
              transition={{ duration: 0.22 }}
            >
              <div className="text-[15px] font-extrabold">{varTitle(current.name, current.description, current.required).title}</div>
              <p className="mt-1.5 whitespace-pre-line text-[12.5px] leading-7 text-muted">
                {varTitle(current.name, current.description, current.required).desc}
              </p>

              {/* suggestion chips */}
              {suggestions.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {(crypto || current.name !== "target" ? suggestions : suggestions.slice(0, 9)).map((s) => (
                    <button
                      key={s}
                      onClick={() => pick(s)}
                      className="rounded-xl border border-line bg-white/[.03] px-3.5 py-2 text-[12.5px] font-semibold transition-all active:scale-95 active:border-brand/50 md:hover:border-brand/40 md:hover:bg-white/[.06]"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}

              {/* fuzzy matches while typing a target */}
              {current.name === "target" && fuzzy.length > 0 && (
                <div className="mt-3 rounded-xl border border-brand/25 bg-brand/5 p-3">
                  <div className="mb-2 text-[11.5px] font-semibold text-indigo-300">🔎 منظورت این بود؟</div>
                  <div className="flex flex-wrap gap-2">
                    {fuzzy.map((s) => (
                      <button key={s} onClick={() => pick(s)} className="rounded-lg bg-brand/15 px-3 py-1.5 text-[12px] font-bold text-indigo-200 active:scale-95">
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* free text */}
              <div className="mt-4 flex gap-2">
                <input
                  ref={inputRef}
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && submitFree()}
                  placeholder="یا مقدار دلخواهت را بنویس…"
                  className="h-11 flex-1 rounded-xl border border-line bg-white/[.04] px-4 text-[14px] outline-none transition-colors placeholder:text-muted/60 focus:border-brand/50"
                />
                <Button size="md" onClick={submitFree} disabled={!freeText.trim()}>ثبت</Button>
              </div>

              {!current.required && (
                <button onClick={skip} className="mt-3 text-[12px] font-semibold text-muted transition-colors hover:text-ink">
                  ⏭ رد کردن (اختیاری)
                </button>
              )}
            </motion.div>
          </AnimatePresence>
        )}

        {launchError && (
          <div className="mt-4 rounded-xl border border-neg/25 bg-neg/10 p-3.5 text-[12.5px] font-semibold text-neg">
            ⚠️ {launchError}
          </div>
        )}
      </Card>

      {launching && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md">
          <div className="rounded-2xl border border-line bg-panel2 px-8 py-7 text-center">
            <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-[3px] border-white/10 border-t-brand" />
            <div className="text-[14px] font-bold">در حال آماده‌سازی تیم…</div>
            <div className="mt-1 text-[12px] text-muted">ایجنت‌ها در حال شکل‌گیری هستند</div>
          </div>
        </div>
      )}
    </div>
  );
}

/* =============================== run history =============================== */
/* Mirrors the bot's swhist (paginated list) + swrun (detail + PDF) flow. */

const SWARM_STATUS_FA: Record<string, { label: string; tone: "success" | "failed" | "running" | "chat" }> = {
  completed: { label: "تکمیل شد", tone: "success" },
  running: { label: "در حال اجرا", tone: "running" },
  in_progress: { label: "در حال اجرا", tone: "running" },
  failed: { label: "ناموفق", tone: "failed" },
  cancelled: { label: "لغو شد", tone: "chat" },
};

function SwarmHistory({ onOpen }: { onOpen: (id: string) => void }) {
  const [runs, setRuns] = useState<SwarmRunRow[] | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const PER = 5;

  useEffect(() => {
    listSwarmRuns()
      .then((r) => setRuns(Array.isArray(r) ? r : []))
      .catch((e) => setError(e.message || "خطا در دریافت تاریخچه"));
  }, []);

  if (!runs && !error) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-xl2 border border-line bg-panel/50 p-5">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="mt-2 h-3 w-32" />
          </div>
        ))}
      </div>
    );
  }
  if (error) return <EmptyState icon="⚠️" title="خطا در دریافت تاریخچه" desc={error} />;
  if (!runs!.length) {
    return (
      <EmptyState
        icon="📭"
        title="هنوز اجرایی نداری"
        desc="از تب «اجرای جدید» یک تیم انتخاب و اجرا کن — نتیجه اینجا ثبت می‌شود."
      />
    );
  }

  const maxPage = Math.max(0, Math.ceil(runs!.length / PER) - 1);
  const chunk = runs!.slice(page * PER, page * PER + PER);

  return (
    <div className="space-y-3">
      {chunk.map((r) => {
        const meta = SWARM_STATUS_FA[r.status || ""] || { label: r.status || "؟", tone: "chat" as const };
        return (
          <motion.button
            key={r.id}
            whileTap={{ scale: 0.985 }}
            onClick={() => setOpenId(openId === r.id ? null : r.id)}
            className="w-full rounded-xl2 border border-line bg-panel/70 p-4 text-right backdrop-blur-xl transition-all duration-300 active:border-brand/50 md:hover:border-brand/40 md:p-5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-extrabold">🤖 {r.preset_name || "تیم"}</div>
                <div className="mt-1 text-[11.5px] text-muted">
                  {r.created_at ? faNum(new Date(r.created_at).toLocaleString("fa-IR")) : ""}
                </div>
              </div>
              <Badge tone={meta.tone}>
                <StatusDot status={meta.tone} /> {meta.label}
              </Badge>
            </div>
            <AnimatePresence initial={false}>
              {openId === r.id && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25 }}
                  className="overflow-hidden"
                  onClick={(e) => e.stopPropagation()}
                >
                  <SwarmHistoryDetail id={r.id} status={r.status} onOpen={onOpen} />
                </motion.div>
              )}
            </AnimatePresence>
          </motion.button>
        );
      })}
      {maxPage > 0 && (
        <div className="flex items-center justify-center gap-2 pt-1">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="rounded-xl border border-line bg-white/[.03] px-4 py-2 text-[12.5px] font-bold text-muted transition-all active:scale-95 disabled:opacity-40"
          >
            ◀ قبلی
          </button>
          <span className="text-[12px] font-bold text-muted">📄 {faNum(page + 1)}/{faNum(maxPage + 1)}</span>
          <button
            onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
            disabled={page === maxPage}
            className="rounded-xl border border-line bg-white/[.03] px-4 py-2 text-[12.5px] font-bold text-muted transition-all active:scale-95 disabled:opacity-40"
          >
            بعدی ▶
          </button>
        </div>
      )}
    </div>
  );
}

function SwarmHistoryDetail({ id, status, onOpen }: { id: string; status?: string; onOpen: (id: string) => void }) {
  const [detail, setDetail] = useState<SwarmRunStatus | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const running = (status || detail?.status) === "running" || (status || detail?.status) === "in_progress";

  useEffect(() => {
    getSwarmRun(id).then(setDetail).catch(() => setDetail({ status: "failed", error: "خطا در بارگذاری" }));
  }, [id]);

  if (!detail) return <div className="mt-3 flex justify-center"><Skeleton className="h-4 w-32" /></div>;
  const report = detail.final_report || "";
  const tasks = detail.tasks || [];

  return (
    <div className="mt-3 border-t border-line pt-3">
      {tasks.length > 0 && (
        <div className="mb-3 space-y-1.5">
          {tasks.map((t, i) => (
            <div key={i} className="flex items-center justify-between rounded-lg bg-white/[.02] px-3 py-2 text-[12px]">
              <span className="font-semibold">{t.agent_name || "ایجنت"}</span>
              <span>{TASK_ICON[t.status || ""] || "⏳"}</span>
            </div>
          ))}
        </div>
      )}
      {report ? (
        <div className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-xl bg-white/[.02] p-3 text-[12.5px] leading-7 text-ink/90">
          {report.slice(0, 600)}{report.length > 600 ? "…" : ""}
        </div>
      ) : running ? (
        <div className="text-[12px] text-muted">🔄 هنوز در حال اجراست — گزارش نهایی بعد از تکمیل می‌آید.</div>
      ) : null}
      <div className="mt-3 flex gap-2">
        {report && (
          <Button size="sm" onClick={() => { setPdfBusy(true); downloadSwarmPdf(id).catch(() => {}).finally(() => setPdfBusy(false)); }} disabled={pdfBusy}>
            <Download size={14} /> {pdfBusy ? "در حال ساخت…" : "دانلود PDF کامل"}
          </Button>
        )}
        {running && (
          <>
            <Button size="sm" variant="outline" onClick={() => getSwarmRun(id).then(setDetail)}>
              <RefreshCw size={14} /> رفرش وضعیت
            </Button>
            <Button size="sm" variant="outline" onClick={() => onOpen(id)}>
              <Zap size={14} /> مشاهده زنده
            </Button>
          </>
        )}
        {!running && !report && (
          <div className="text-[12px] text-muted">گزارشی ثبت نشده است.</div>
        )}
      </div>
    </div>
  );
}

/* =============================== live tracker ============================== */

function SwarmTracker({ runId, presetName, onDone }: {
  runId: string; presetName: string; onDone: () => void;
}) {
  const [status, setStatus] = useState<string>("running");
  const [tasks, setTasks] = useState<Array<{ agent_name?: string; status?: string }>>([]);
  const [report, setReport] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      setElapsed((e) => e + 1);
      try {
        const s = await getSwarmRun(runId);
        if (cancelled) return;
        setTasks(s.tasks || []);
        setStatus(s.status || "running");
        if (s.status === "completed") {
          setReport(s.final_report || "");
          clearInterval(timer.current!);
          onDone();
        }
        if (s.status === "failed") clearInterval(timer.current!);
      } catch { /* transient */ }
    };
    const t0 = setInterval(tick, 6000);
    timer.current = t0;
    tick();
    return () => { cancelled = true; clearInterval(t0); };
  }, [runId]); // eslint-disable-line

  const done = tasks.filter((t) => t.status === "completed").length;
  const total = tasks.length;

  return (
    <div className="space-y-4">
      <Card className="p-5 md:p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-[14.5px] font-extrabold">
            <Zap size={17} className="text-brand" /> {presetName}
          </div>
          <Badge tone={status === "completed" ? "success" : status === "failed" ? "failed" : "running"}>
            <StatusDot status={status === "completed" ? "success" : status === "failed" ? "failed" : "running"} />
            {status === "completed" ? "تکمیل شد" : status === "failed" ? "ناموفق" : "در حال اجرا"}
          </Badge>
        </div>

        <div className="mt-4">
          <div className="mb-2 flex justify-between text-[12px] text-muted">
            <span>پیشرفت ایجنت‌ها</span>
            <span className="font-bold text-ink">{faNum(done)}/{faNum(total)}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-white/5">
            <motion.div
              className="h-full rounded-full bg-gradient-to-l from-brand to-brand-soft"
              animate={{ width: `${total ? (done / total) * 100 : 5}%` }}
              transition={{ duration: 0.5 }}
            />
          </div>
        </div>

        <div className="mt-5 space-y-2">
          {tasks.length === 0 && (
            <div className="flex items-center gap-3 rounded-xl border border-line bg-white/[.02] p-3.5 text-[12.5px] text-muted">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/10 border-t-brand" />
              تیم در حال شکل‌گیری است…
            </div>
          )}
          {tasks.map((t, i) => (
            <motion.div
              key={(t.agent_name || "agent") + i}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              className="flex items-center justify-between rounded-xl border border-line bg-white/[.02] px-4 py-3"
            >
              <span className="text-[13px] font-semibold">{t.agent_name || "ایجنت"}</span>
              <span className="text-[13px]">{TASK_ICON[t.status || ""] || "⏳"}</span>
            </motion.div>
          ))}
        </div>

        {status === "running" && (
          <div className="mt-4 text-center text-[11.5px] text-muted">
            ⏳ {faNum(Math.floor(elapsed / 10))} دقیقه — معمولاً ~۲۰ دقیقه طول می‌کشد. این صفحه را باز نگه دار.
          </div>
        )}
        {status === "failed" && (
          <div className="mt-4 rounded-xl border border-neg/25 bg-neg/10 p-3.5 text-[12.5px] font-semibold text-neg">
            ❌ اجرای تیم ناموفق بود. دوباره تلاش کن.
          </div>
        )}
      </Card>

      {report && (
        <Card className="p-5 md:p-6">
          <div className="mb-3 text-[14px] font-extrabold">📋 گزارش نهایی تیم</div>
          <div className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap text-[13px] leading-8 text-ink/90">{report}</div>
        </Card>
      )}
    </div>
  );
}
