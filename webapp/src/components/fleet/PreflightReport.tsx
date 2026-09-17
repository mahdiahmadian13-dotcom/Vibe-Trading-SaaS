export type PreflightReportData = { ready: boolean; checks: Array<{ check: string; ok: boolean; msg_fa: string; autofixed: boolean }> };

export function PreflightReport({ report, loading, error, onRefresh }: {
  report: PreflightReportData | null; loading: boolean; error: string; onRefresh: () => void;
}) {
  return <section dir="rtl" data-testid="preflight-report" aria-busy={loading} className="mt-4 rounded-xl border border-white/10 bg-white/[.02] p-3 text-xs leading-6">
    <div className="flex items-center justify-between gap-3">
      <h4 className="font-bold">پیش‌نیازهای مرکز</h4>
      <button type="button" disabled={loading} onClick={onRefresh} className="rounded-lg border border-white/10 px-2 py-1 disabled:opacity-40">بررسی مجدد</button>
    </div>
    <p className="mt-1 text-[11px] text-muted">این گزارش تنظیمات مرکز را بررسی می‌کند؛ سلامت شبکه و نصب انجین روی گره هنوز تأیید نشده است.</p>
    <div aria-live="polite">
      {loading ? <p className="mt-2 animate-pulse">در حال بررسی…</p> : error ? <p role="alert" className="mt-2 text-red-200">گزارش در دسترس نیست: {error}</p> : report && <>
        <ul className="mt-2 space-y-2">{report.checks.map(c => <li key={c.check} data-testid={`preflight-${c.check}`} className="flex items-start gap-2">
          <span className={c.ok ? "text-emerald-300" : "text-red-300"}>{c.ok ? "✓" : "✕"}</span>
          <span className={c.ok ? "" : "text-red-200"}>{c.msg_fa}{c.autofixed && <span className="mr-1 rounded bg-white/10 px-1.5 text-[10px] text-muted">تأمین خودکار</span>}</span>
        </li>)}</ul>
        <p className={`mt-3 rounded-lg px-2 py-1 ${report.ready ? "bg-emerald-500/10 text-emerald-200" : "bg-red-500/10 text-red-200"}`}>{report.ready ? "تنظیمات مرکز آماده است" : "نصب متوقف است — موارد ناموفق را برطرف و دوباره بررسی کنید."}</p>
      </>}
    </div>
  </section>;
}
