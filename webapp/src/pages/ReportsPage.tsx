import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { BarChart3, Copy, Download, FileCode2, FileText, X } from "lucide-react";
import { api, auth, authDownload, getRun, getRuns, type RunDetail, type RunRow } from "@/api/client";
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
    const bts = list.filter((r) => r.total_return != null || r.sharpe != null);
    const wins = bts.filter((r) => (r.total_return ?? 0) > 0).length;
    const avg = bts.length ? bts.reduce((a, r) => a + (r.total_return ?? 0), 0) / bts.length : null;
    return { total: list.length, bts: bts.length, wins, avg };
  }, [runs]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-28 pt-5 md:px-8 md:pb-16 md:pt-8">
      <header className="mb-6 md:mb-8">
        <h1 className="flex items-center gap-2 text-[19px] font-extrabold tracking-tight md:text-[22px]">
          <BarChart3 size={20} className="text-brand" /> گزارش‌های بک‌تست
        </h1>
        <p className="mt-1.5 text-[12.5px] leading-6 text-muted md:text-[13.5px]">
          نتایج بک‌تست‌ها، نمودار ارزش پرتفوی، کد استراتژی و گزارش PDF
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
          title="هنوز بک‌تستی نداری"
          desc="از بخش چت شروع کن — استراتژی‌ات را بگو تا تیم AI بک‌تست بگیرد."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 md:gap-4">
            <StatCard label="کل اجراها" value={faNum(stats.total)} />
            <StatCard label="بک‌تست‌های واقعی" value={faNum(stats.bts)} tone="brand" />
            <StatCard label="سودده" value={faNum(stats.wins)} tone="pos" />
            <StatCard
              label="میانگین بازده"
              value={stats.avg == null ? "—" : fmtPct(stats.avg)}
              tone={stats.avg == null ? undefined : stats.avg >= 0 ? "pos" : "neg"}
            />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 md:gap-4 lg:grid-cols-3">
            {runs.map((r) => {
              const hasM = r.total_return != null || r.sharpe != null;
              return (
                <motion.button
                  key={r.run_id}
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  whileTap={{ scale: 0.985 }}
                  transition={{ duration: 0.3 }}
                  onClick={() => setOpenId(r.run_id)}
                  className={`w-full rounded-xl2 border border-line bg-panel/70 p-4 text-right backdrop-blur-xl transition-all duration-300 active:border-brand/50 md:hover:-translate-y-1 md:hover:border-brand/40 md:hover:shadow-[0_16px_40px_-12px_rgba(0,0,0,.6)] md:p-5 ${hasM ? "" : "opacity-70"}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Badge tone={hasM ? "bt" : "chat"}>{hasM ? "بک‌تست" : "چت"}</Badge>
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
  const lineCount = activeSrc ? activeSrc.split("\n").length : 0;

  const copyCode = () => {
    navigator.clipboard.writeText(activeSrc).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 1500); },
      () => {},
    );
  };

  const downloadPdf = () => {
    setPdfBusy(true);
    authDownload(`/api/v1/vibe/runs/${runId}/pdf?_=${Date.now()}`, `backtest_${runId}.pdf`)
      .catch(() => {}).finally(() => setPdfBusy(false));
  };
  const downloadCode = () => {
    authDownload(
      `/api/v1/vibe/runs/${runId}/code/download?file=${encodeURIComponent(activeFile)}&_=${Date.now()}`,
      activeFile,
    ).catch(() => {});
  };

  const metricBox = (label: string, value: string | null, tone?: string) =>
    value == null ? null : (
      <div className="rounded-xl border border-line bg-panel/70 p-3.5">
        <div className="text-[11.5px] font-semibold text-muted">{label}</div>
        <div className={`mt-1 text-[18px] font-extrabold ${tone || ""}`}>{value}</div>
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
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[15px] font-extrabold leading-7">
              {hasMetrics ? "📊 گزارش بک‌تست" : "💬 خروجی"} — {detail?.prompt || runId}
            </div>
            <div dir="ltr" className="mt-0.5 text-right text-[11px] text-muted/70">{runId}</div>
          </div>
          <button onClick={onClose} aria-label="بستن"
            className="shrink-0 rounded-lg border border-line bg-white/[.03] p-2 text-muted transition-colors hover:text-ink">
            <X size={16} />
          </button>
        </div>

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
            {hasMetrics && (
              <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
                {metricBox("بازده کل", m.total_return != null ? fmtPct(m.total_return) : null, fmtCls(m.total_return))}
                {metricBox("سالانه", m.annual_return != null ? fmtPct(m.annual_return) : null, fmtCls(m.annual_return))}
                {metricBox("شارپ", m.sharpe != null ? m.sharpe.toFixed(2) : null)}
                {metricBox("سورتینو", m.sortino != null ? m.sortino.toFixed(2) : null)}
                {metricBox("حداکثر افت", m.max_drawdown != null ? fmtPct(m.max_drawdown) : null, "text-neg")}
                {metricBox("نرخ برد", m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : null)}
                {metricBox("معاملات", m.trade_count != null ? faNum(m.trade_count) : null)}
                {metricBox("ارزش نهایی", m.final_value != null ? `$${Number(m.final_value).toLocaleString()}` : null)}
              </div>
            )}

            {chartUrl && (
              <img src={chartUrl} alt="نمودار ارزش پرتفوی" className="mt-4 w-full rounded-xl border border-line" />
            )}

            {hasMetrics && (
              <div className="mt-4 flex flex-col gap-2 sm:flex-row">
                <Button className="flex-1" onClick={downloadPdf} disabled={pdfBusy}>
                  <FileText size={15} /> {pdfBusy ? "در حال آماده‌سازی…" : "دانلود PDF"}
                </Button>
                <Button variant="outline" className="flex-1" onClick={loadCode}>
                  <FileCode2 size={15} /> {codeOpen ? "بستن کد استراتژی" : "مشاهده کد استراتژی"}
                </Button>
              </div>
            )}

            <AnimatePresence initial={false}>
              {codeOpen && code && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
                  className="overflow-hidden"
                >
                  <div className="mt-4 border-t border-line pt-4">
                    {codeNames.length === 0 ? (
                      <div className="text-[12.5px] text-muted">کد استراتژی برای این گزارش موجود نیست.</div>
                    ) : (
                      <>
                        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
                          {codeNames.map((n) => (
                            <button
                              key={n}
                              onClick={() => setActiveFile(n)}
                              className={`rounded-lg border px-3 py-1.5 font-mono text-[11px] transition-colors ${n === activeFile ? "border-brand/50 bg-brand/15 text-indigo-200" : "border-line text-muted hover:text-ink"}`}
                            >
                              {n}
                            </button>
                          ))}
                          <span dir="ltr" className="mr-auto text-[10.5px] text-muted/70">{lineCount} lines</span>
                        </div>
                        <pre dir="ltr" className="max-h-[38dvh] overflow-auto rounded-xl border border-line bg-black/40 p-4 text-left font-mono text-[11.5px] leading-7 text-slate-200">
                          {activeSrc || "—"}
                        </pre>
                        <div className="mt-2.5 flex gap-2">
                          <Button size="sm" variant="outline" onClick={copyCode}>
                            <Copy size={13} /> {copied ? "کپی شد ✅" : "کپی کد"}
                          </Button>
                          <Button size="sm" variant="outline" onClick={downloadCode}>
                            <Download size={13} /> دانلود {activeFile.endsWith(".pine") ? ".pine" : ".py"}
                          </Button>
                        </div>
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
