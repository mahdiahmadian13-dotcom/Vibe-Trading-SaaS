import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  BarChart3, Copy, Download, FileCode2, FileText, X, TrendingUp, Percent,
  Activity, Target, Trophy, Wallet, Flame, Scale, Calendar, Check, Code2, Send,
} from "lucide-react";
import { api, auth, inTelegramWebApp, openDownloadToken, smartDownload, getRun, getRuns, type RunDetail, type RunRow } from "@/api/client";
import { faNum, fmtCls, fmtPct } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { CardSkeleton, EmptyState, StatCard } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/Badge";

type CodeData = { files?: Record<string, string>; pine?: { exists?: boolean; content?: string | null } };

/** Blob URL for an authed GET (chart PNG). Caller must revoke. */
async function authBlobUrl(url: string): Promise<string> {
  const r = await fetch(url, { headers: { Authorization: "Bearer " + auth.token } });
  if (!r.ok) throw new Error(`خطای سرور: ${r.status}`);
  return URL.createObjectURL(await r.blob());
}

const statusFa = (s?: string) =>
  s === "success" ? "موفق" : s === "running" ? "در حال اجرا" : s === "failed" ? "ناموفق" : s || "؟";
const statusTone = (s?: string): "success" | "running" | "failed" =>
  s === "success" ? "success" : s === "running" ? "running" : "failed";

export default function ReportsPage() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    getRuns().then(setRuns).catch((e) => setError(e.message));
  }, []);

  const stats = useMemo(() => {
    const list = runs || [];
    const wins = list.filter((r) => (r.total_return ?? 0) > 0).length;
    const avg = list.length ? list.reduce((a, r) => a + (r.total_return ?? 0), 0) / list.length : null;
    return { total: list.length, wins, avg };
  }, [runs]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-28 pt-5 md:px-8 md:pb-16 md:pt-8">
      <header className="mb-6 md:mb-8">
        <h1 className="flex items-center gap-2 text-[19px] font-extrabold tracking-tight md:text-[22px]">
          <BarChart3 size={20} className="text-brand" /> گزارش‌های بک‌تست
        </h1>
        <p className="mt-1.5 text-[12.5px] leading-6 text-muted md:text-[13.5px]">
          نتایج بک‌تست‌های تکمیل‌شده — نمودار ارزش پرتفوی، کد استراتژی و گزارش PDF
        </p>
      </header>

      {error ? (
        <EmptyState icon="⚠️" title="خطا در دریافت گزارش‌ها" desc={error} />
      ) : runs == null ? (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 md:gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="rounded-xl2 border border-line bg-panel/50 p-4 md:p-5">
                <div className="skeleton h-3 w-20" />
                <div className="skeleton mt-3 h-6 w-16" />
              </div>
            ))}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 md:gap-4 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
          </div>
        </>
      ) : runs.length === 0 ? (
        <EmptyState
          icon="📭"
          title="هنوز بک‌تستی تکمیل نشده"
          desc="از بخش چت شروع کن — وقتی بک‌تست انجام شود، گزارش کامل اینجا ظاهر می‌شود (PDF + کد استراتژی)."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 md:gap-4">
            <StatCard label="بک‌تست‌های تکمیل‌شده" value={faNum(stats.total)} tone="brand" />
            <StatCard label="سودده" value={faNum(stats.wins)} tone="pos" />
            <StatCard
              label="میانگین بازده"
              value={stats.avg == null ? "—" : fmtPct(stats.avg)}
              tone={stats.avg == null ? undefined : stats.avg >= 0 ? "pos" : "neg"}
            />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 md:gap-4 lg:grid-cols-3">
            {runs.map((r) => {
              const pos = (r.total_return ?? 0) > 0;
              return (
                <motion.button
                  key={r.run_id}
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  whileTap={{ scale: 0.985 }}
                  transition={{ duration: 0.3 }}
                  onClick={() => setOpenId(r.run_id)}
                  className="group w-full rounded-xl2 border border-line bg-panel/70 p-4 text-right backdrop-blur-xl transition-all duration-300 active:border-brand/50 md:hover:-translate-y-1 md:hover:border-brand/40 md:hover:shadow-[0_16px_40px_-12px_rgba(0,0,0,.6)] md:p-5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Badge tone="bt">بک‌تست</Badge>
                      <Badge tone={statusTone(r.status)}>{statusFa(r.status)}</Badge>
                    </div>
                    <span dir="ltr" className="truncate text-[10px] text-muted/60">{r.run_id}</span>
                  </div>
                  <div className="mt-3 line-clamp-2 min-h-[40px] text-[13px] font-bold leading-6.5 md:min-h-[44px] md:text-[13.5px] md:leading-7">
                    {r.prompt || "—"}
                  </div>
                  <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] md:mt-3 md:text-[12px]">
                    {r.total_return != null && (
                      <span className={`font-extrabold ${fmtCls(r.total_return)}`}>
                        {fmtPct(r.total_return)} بازده
                      </span>
                    )}
                    {r.sharpe != null && <span className="text-muted">شارپ {r.sharpe.toFixed(2)}</span>}
                    {(r.start_date || r.end_date) && (
                      <span dir="ltr" className="text-muted/80">{r.start_date} → {r.end_date}</span>
                    )}
                  </div>
                  <div className={`mt-3 h-1 w-full overflow-hidden rounded-full bg-white/[.06] ${pos ? "" : "opacity-70"}`}>
                    <div
                      className={`h-full rounded-full ${pos ? "bg-gradient-to-l from-emerald-400 to-emerald-600" : "bg-gradient-to-l from-rose-400 to-rose-600"}`}
                      style={{ width: `${Math.min(100, Math.abs(r.total_return ?? 0) * 2)}%` }}
                    />
                  </div>
                </motion.button>
              );
            })}
          </div>
        </>
      )}

      <AnimatePresence>
        {openId && <ReportModal runId={openId} onClose={() => setOpenId(null)} />}
      </AnimatePresence>
    </div>
  );
}

/* --------------------------------- modal ---------------------------------- */

function ReportModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  const [chartUrl, setChartUrl] = useState<string | null>(null);
  const [code, setCode] = useState<CodeData | null>(null);
  const [codeOpen, setCodeOpen] = useState(false);
  const [activeFile, setActiveFile] = useState("");
  const [copied, setCopied] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);

  useEffect(() => {
    getRun(runId).then(setDetail).catch((e) => setErr(e.message));
    let revoked = false;
    let url = "";
    authBlobUrl(`/api/v1/vibe/runs/${runId}/chart?_=${Date.now()}`)
      .then((u) => { if (!revoked) { url = u; setChartUrl(u); } else URL.revokeObjectURL(u); })
      .catch(() => {});
    return () => { revoked = true; if (url) URL.revokeObjectURL(url); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // lock body scroll while open
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  const m = detail?.metrics || {};
  const hasMetrics = m.total_return != null || m.sharpe != null || m.trade_count != null;

  const loadCode = async () => {
    if (code) { setCodeOpen((v) => !v); return; }
    try {
      const d = await api<CodeData>(`/api/v1/vibe/runs/${runId}/code`);
      setCode(d);
      const names = Object.keys(d.files || {});
      if (d.pine?.exists) names.push("strategy.pine");
      setActiveFile(names[0] || "");
      setCodeOpen(true);
    } catch (e) { setErr((e as Error).message); }
  };

  const codeNames = (() => {
    const names = Object.keys(code?.files || {});
    if (code?.pine?.exists && !names.includes("strategy.pine")) names.push("strategy.pine");
    return names;
  })();
  const activeSrc = activeFile === "strategy.pine" ? code?.pine?.content || "" : code?.files?.[activeFile] || "";
  const codeLines = activeSrc ? activeSrc.split("\n") : [];

  const copyCode = () => {
    navigator.clipboard.writeText(activeSrc).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 1500); },
      () => {},
    );
  };

  const downloadPdf = async () => {
    setPdfBusy(true);
    setNotice("");
    try {
      if (inTelegramWebApp()) {
        // 1) official Telegram native popup (downloadFile) — best UX
        const ok = await openDownloadToken(`/api/v1/vibe/runs/${runId}/pdf-token`, { kind: "pdf" });
        if (ok) {
          setNotice("✅ دانلود شد — در مسیر Download/Telegram (اندروید) یا Files ← Telegram (آیفون) ذخیره می‌شود");
          return;
        }
        // 2) fallback: bot sends the file straight into the chat — always works
        const r = await api<Record<string, unknown>>(`/api/v1/vibe/runs/${runId}/send-to-telegram`, { method: "POST" });
        if (!r?.ok) throw new Error("send failed");
        setNotice("✅ گزارش PDF به چت تلگرام شما ارسال شد");
        return;
      }
      const ok = await smartDownload(
        `/api/v1/vibe/runs/${runId}/pdf?_=${Date.now()}`,
        `backtest_${runId}.pdf`,
        `/api/v1/vibe/runs/${runId}/pdf-token`,
        { kind: "pdf" },
      );
      if (!ok) setErr("دانلود ناموفق بود — دوباره تلاش کن");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ارسال به تلگرام ناموفق بود");
    } finally {
      setPdfBusy(false);
    }
  };
  /** Telegram-only: deliver the PDF into the chat (persistent, easy to share). */
  const sendPdfToChat = async () => {
    try {
      const r = await api<Record<string, unknown>>(`/api/v1/vibe/runs/${runId}/send-to-telegram`, { method: "POST" });
      if (!r?.ok) throw new Error("send failed");
      setErr("");
      setNotice("✅ گزارش PDF به چت تلگرام شما ارسال شد");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ارسال به تلگرام ناموفق بود");
    }
  };
  const downloadCode = async () => {
    try {
      if (inTelegramWebApp()) {
        const ok = await openDownloadToken(`/api/v1/vibe/runs/${runId}/pdf-token`, {
          kind: "code",
          file: activeFile,
        });
        if (ok) {
          setNotice("✅ دانلود شد — در مسیر Download/Telegram (اندروید) یا Files ← Telegram (آیفون) ذخیره می‌شود");
          return;
        }
        const r = await api<Record<string, unknown>>(`/api/v1/vibe/runs/${runId}/code/send-to-telegram`, {
          method: "POST",
          body: JSON.stringify({ file: activeFile }),
        });
        if (!r?.ok) throw new Error("send failed");
        setNotice("✅ کد استراتژی به چت تلگرام شما ارسال شد");
        return;
      }
      const ok = await smartDownload(
        `/api/v1/vibe/runs/${runId}/code/download?file=${encodeURIComponent(activeFile)}&_=${Date.now()}`,
        activeFile,
        `/api/v1/vibe/runs/${runId}/pdf-token`,
        { kind: "code", file: activeFile },
      );
      if (!ok) setErr("دانلود ناموفق بود — دوباره تلاش کن");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ارسال به تلگرام ناموفق بود");
    }
  };
  /** Telegram-only: deliver the code file into the chat. */
  const sendCodeToChat = async () => {
    try {
      const r = await api<Record<string, unknown>>(`/api/v1/vibe/runs/${runId}/code/send-to-telegram`, {
        method: "POST",
        body: JSON.stringify({ file: activeFile }),
      });
      if (!r?.ok) throw new Error("send failed");
      setErr("");
      setNotice("✅ کد استراتژی به چت تلگرام شما ارسال شد");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ارسال به تلگرام ناموفق بود");
    }
  };

  const metricBox = (
    label: string, value: string | null, tone?: string, Icon?: typeof TrendingUp,
  ) =>
    value == null ? null : (
      <div className="group rounded-xl border border-line bg-gradient-to-b from-white/[.04] to-transparent p-3 transition-all hover:border-brand/30">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-muted">
          {Icon && <Icon size={13} className="text-brand/80" />}
          {label}
        </div>
        <div className={`mt-1.5 text-[17px] font-extrabold tracking-tight ${tone || ""}`}>{value}</div>
      </div>
    );

  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-md md:items-center md:p-6"
      onClick={onClose}
    >
      <motion.div
        initial={{ y: 60, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 60, opacity: 0 }}
        transition={{ type: "spring", stiffness: 320, damping: 32 }}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[92dvh] w-full max-w-2xl overflow-y-auto rounded-t-3xl border border-line bg-panel2 p-5 md:rounded-3xl md:p-7"
      >
        {/* header */}
        <div className="mb-5 flex items-start justify-between gap-3 border-b border-line pb-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-brand/15 ring-1 ring-inset ring-brand/30">
                <BarChart3 size={16} className="text-brand" />
              </span>
              <div className="text-[14.5px] font-extrabold leading-6">گزارش بک‌تست</div>
            </div>
            <div className="mt-2 line-clamp-2 text-[12.5px] leading-6 text-muted">
              {detail?.prompt || "—"}
            </div>
            <div dir="ltr" className="mt-1 text-right text-[10.5px] text-muted/60">{runId}</div>
          </div>
          <button onClick={onClose} aria-label="بستن"
            className="shrink-0 rounded-lg border border-line bg-white/[.03] p-2 text-muted transition-colors hover:text-ink">
            <X size={16} />
          </button>
        </div>

        {notice ? (
          <div className="rounded-xl border border-pos/25 bg-pos/10 p-4 text-[13px] font-semibold text-pos">{notice}</div>
        ) : null}
        {err ? (
          <div className="rounded-xl border border-neg/25 bg-neg/10 p-4 text-[13px] font-semibold text-neg">❌ {err}</div>
        ) : !detail ? (
          <div className="space-y-3">
            <div className="skeleton h-16 w-full" />
            <div className="skeleton h-32 w-full" />
            <div className="skeleton h-10 w-1/2" />
          </div>
        ) : (
          <>
            {/* metrics grid */}
            {hasMetrics && (
              <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
                {metricBox("بازده کل", m.total_return != null ? fmtPct(m.total_return) : null, fmtCls(m.total_return), TrendingUp)}
                {metricBox("بازده سالانه", m.annual_return != null ? fmtPct(m.annual_return) : null, fmtCls(m.annual_return), Percent)}
                {metricBox("شارپ", m.sharpe != null ? m.sharpe.toFixed(2) : null, undefined, Activity)}
                {metricBox("سورتینو", m.sortino != null ? m.sortino.toFixed(2) : null, undefined, Scale)}
                {metricBox("حداکثر افت", m.max_drawdown != null ? fmtPct(m.max_drawdown) : null, "text-neg", Flame)}
                {metricBox("نرخ برد", m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : null, undefined, Target)}
                {metricBox("تعداد معاملات", m.trade_count != null ? faNum(m.trade_count) : null, undefined, Trophy)}
                {metricBox("ارزش نهایی", m.final_value != null ? `$${Number(m.final_value).toLocaleString()}` : null, undefined, Wallet)}
              </div>
            )}

            {/* equity chart */}
            {chartUrl && (
              <div className="mt-4 overflow-hidden rounded-xl border border-line bg-black/30">
                <div className="flex items-center gap-2 border-b border-line px-3.5 py-2.5 text-[11.5px] font-bold text-muted">
                  <TrendingUp size={13} className="text-brand" /> نمودار ارزش پرتفوی (Equity Curve)
                </div>
                <img src={chartUrl} alt="نمودار ارزش پرتفوی" className="w-full" />
              </div>
            )}

            {/* action buttons */}
            <div className="mt-5 flex flex-col gap-3 sm:flex-row">
              <Button size="lg" className="flex-1 gap-2" onClick={downloadPdf} disabled={pdfBusy}>
                <FileText size={17} />
                {pdfBusy ? "در حال آماده‌سازی…" : "دانلود گزارش PDF"}
              </Button>
              <Button size="lg" variant="outline" className="flex-1 gap-2" onClick={loadCode}>
                <Code2 size={17} />
                {codeOpen ? "بستن کد استراتژی" : "کد استراتژی"}
              </Button>
            </div>

            {/* inside Telegram: also offer direct chat delivery (file lands in the bot chat) */}
            {inTelegramWebApp() && (
              <Button size="sm" variant="outline" className="mt-2 w-full gap-2" onClick={sendPdfToChat}>
                <Send size={14} />
                دریافت PDF در چت تلگرام (برای اشتراک‌گذاری)
              </Button>
            )}

            {/* code viewer */}
            <AnimatePresence initial={false}>
              {codeOpen && code && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
                  className="overflow-hidden"
                >
                  <div className="mt-4 rounded-xl border border-line bg-black/40">
                    {codeNames.length === 0 ? (
                      <div className="p-4 text-[12.5px] text-muted">کد استراتژی برای این گزارش موجود نیست.</div>
                    ) : (
                      <>
                        {/* file tabs */}
                        <div className="flex items-center gap-1.5 border-b border-line px-3 py-2.5">
                          {codeNames.map((n) => (
                            <button
                              key={n}
                              onClick={() => setActiveFile(n)}
                              className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 font-mono text-[11px] transition-colors ${n === activeFile ? "border-brand/50 bg-brand/15 text-indigo-200" : "border-line text-muted hover:text-ink"}`}
                            >
                              <FileCode2 size={11} />
                              {n}
                            </button>
                          ))}
                          <span dir="ltr" className="mr-auto text-[10.5px] text-muted/70">{codeLines.length} lines</span>
                        </div>
                        {/* code with line numbers */}
                        <pre dir="ltr" className="max-h-[38dvh] overflow-auto p-4 text-left font-mono text-[11.5px] leading-7 text-slate-200">
                          <code>
                            {codeLines.map((line, i) => (
                              <div key={i} className="flex">
                                <span className="w-10 shrink-0 select-none pr-3 text-right text-slate-600">{i + 1}</span>
                                <span className="whitespace-pre">{line || " "}</span>
                              </div>
                            ))}
                          </code>
                        </pre>
                        {/* code actions */}
                        <div className="flex flex-col gap-2.5 border-t border-line p-3 sm:flex-row">
                          <Button variant="outline" className="flex-1 gap-2" onClick={copyCode}>
                            {copied ? <Check size={15} /> : <Copy size={15} />}
                            {copied ? "کپی شد ✅" : "کپی کد"}
                          </Button>
                          <Button variant="outline" className="flex-1 gap-2" onClick={downloadCode}>
                            <Download size={15} />
                            دانلود {activeFile.endsWith(".pine") ? ".pine" : ".py"}
                          </Button>
                        </div>
                        {inTelegramWebApp() && (
                          <Button size="sm" variant="ghost" className="w-full gap-2 border-t border-line" onClick={sendCodeToChat}>
                            <Send size={14} />
                            دریافت {activeFile.endsWith(".pine") ? "Pine" : "کد"} در چت تلگرام
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </>
        )}
      </motion.div>
    </motion.div>
  );
}
