export type RunRow = {
  run_id: string;
  prompt?: string;
  status?: string;
  total_return?: number | null;
  sharpe?: number | null;
  start_date?: string;
  end_date?: string;
  created_at?: string;
};

export type Metrics = {
  total_return?: number | null;
  annual_return?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown?: number | null;
  win_rate?: number | null;
  trade_count?: number | null;
  final_value?: number | null;
};

export type RunDetail = {
  run_id: string;
  prompt?: string;
  status?: string;
  session_id?: string;
  metrics?: Metrics;
  equity_curve?: Array<{ t?: string; value?: number } | number[]> | null;
};

export type SessionRow = { id?: string; vibe_session_id?: string; title?: string };

const TOKEN_KEY = "vt_token";
const USER_KEY = "vt_username";

export const auth = {
  get token() { return localStorage.getItem(TOKEN_KEY) || ""; },
  get username() { return localStorage.getItem(USER_KEY) || ""; },
  set(username: string, token: string) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, username);
  },
  clear() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); },
};

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers = new Headers({ "Content-Type": "application/json", ...(opts.headers || {}) });
  if (auth.token) headers.set("Authorization", `Bearer ${auth.token}`);
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { auth.clear(); window.location.reload(); throw new ApiError("unauthorized", 401); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError((data as { detail?: string }).detail || "خطای غیرمنتظره", r.status);
  return data as T;
}

/* ---------------------------------------------------------------------------
 * Telegram WebApp auto-login (bridge to /api/v1/auth/telegram)
 * ------------------------------------------------------------------------ */

type TelegramWebApp = {
  initData?: string;
  initDataUnsafe?: { start_param?: string; user?: { id: number } };
  ready?: () => void;
  expand?: () => void;
  HapticFeedback?: { impactOccurred?: (s: string) => void };
  downloadFile?: (params: { url: string; file_name: string }, callback?: (accepted: boolean) => void) => void;
  openLink?: (url: string, options?: { try_instant_view?: boolean }) => void;
  platform?: string;
};

declare global {
  interface Window { Telegram?: { WebApp?: TelegramWebApp } }
}

/** Extract ?ref=… / start_param from URL or Telegram deep-link payload. */
function tgStartParam(): string | null {
  try {
    const q = new URLSearchParams(location.search);
    const ref = q.get("ref") || q.get("tgWebAppStartParam");
    if (ref) return ref;
    const sp = window.Telegram?.WebApp?.initDataUnsafe?.start_param;
    return sp || null;
  } catch { return null; }
}

/** If running inside the Telegram WebApp, mint a JWT from initData. */
export async function telegramAutoLogin(): Promise<boolean> {
  const tg = window.Telegram?.WebApp;
  if (!tg?.initData || auth.token) {
    if (tg?.ready) tg.ready();
    if (tg?.expand) tg.expand();
    return false;
  }
  try {
    const body: Record<string, string> = { init_data: tg.initData };
    const sp = tgStartParam();
    if (sp) body.start_param = sp;
    const r = await fetch("/api/v1/auth/telegram", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) return false;
    const data = await r.json();
    if (data?.access_token) {
      auth.set(data.username || "کاربر تلگرام", data.access_token);
      return true;
    }
    return false;
  } catch {
    return false;
  } finally {
    if (tg?.ready) tg.ready();
    if (tg?.expand) tg.expand();
  }
}

export const getRuns = (backtestsOnly = true) =>
  api<RunRow[]>(`/api/v1/vibe/runs${backtestsOnly ? "?backtests_only=true" : ""}`);
export const getRun = (id: string) => api<RunDetail>(`/api/v1/vibe/runs/${id}`);
export const getSessions = () => api<SessionRow[]>("/api/v1/vibe/sessions");

/** True when running inside the Telegram WebApp (mobile WebView blocks blob downloads). */
export function inTelegramWebApp(): boolean {
  return !!window.Telegram?.WebApp?.initData;
}

/** Blob download with Bearer auth — plain browsers only (Telegram mobile silently ignores it). */
export async function authDownload(url: string, filename: string) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${auth.token}` } });
  if (!r.ok) throw new ApiError("خطای سرور", r.status);
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/** Telegram-friendly download: mint one-time token URL, then open it natively. */
export async function openDownloadToken(
  url: string,
  opts: { kind?: "pdf" | "code"; file?: string } = {},
): Promise<boolean> {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${auth.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ kind: opts.kind || "pdf", file: opts.file }),
    });
    if (!r.ok) return false;
    const d = await r.json();
    if (!d?.url) return false;
    const full = new URL(d.url, window.location.origin).href;
    const tg = window.Telegram?.WebApp;
    const file_name = opts.kind === "code" ? opts.file || "strategy.py" : "backtest.pdf";
    // 1) official Telegram native download popup (Bot API 8.0+):
    //    downloadFile({url, file_name}, callback) — object signature, HTTPS absolute URL
    if (tg?.downloadFile) {
      return await new Promise<boolean>((resolve) => {
        let settled = false;
        const done = (ok: boolean) => {
          if (!settled) {
            settled = true;
            resolve(ok);
          }
        };
        try {
          tg.downloadFile!({ url: full, file_name }, (accepted) => done(accepted !== false));
          setTimeout(() => done(true), 8000); // popup closed without callback → assume ok
        } catch {
          done(false);
        }
      });
    }
    // 2) Telegram openLink → system browser
    if (tg?.openLink) {
      tg.openLink(full, { try_instant_view: false });
      return true;
    }
    // 3) plain browser
    const a = document.createElement("a");
    a.href = full;
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    return true;
  } catch {
    return false;
  }
}

/**
 * Download that works everywhere:
 * - Telegram WebApp (mobile+desktop): one-time token URL + downloadFile()/openLink
 * - plain browser: Bearer blob download
 */
export async function smartDownload(
  fileUrl: string,
  filename: string,
  tokenUrl: string,
  opts: { kind?: "pdf" | "code"; file?: string } = {},
): Promise<boolean> {
  if (inTelegramWebApp()) {
    return openDownloadToken(tokenUrl, opts);
  }
  try {
    await authDownload(fileUrl, filename);
    return true;
  } catch {
    return openDownloadToken(tokenUrl, opts);
  }
}
