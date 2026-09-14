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

/** Blob download with Bearer auth (browser can't send headers via <a download>). */
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
    const tg = (window as unknown as {
      Telegram?: { WebApp?: { openLink?: (l: string, o?: object) => void } };
    }).Telegram?.WebApp;
    if (tg?.openLink) {
      // inside Telegram WebApp: openLink opens the system browser (downloads work there)
      tg.openLink(full, { try_instant_view: false });
    } else {
      const a = document.createElement("a");
      a.href = full;
      a.target = "_blank";
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    return true;
  } catch {
    return false;
  }
}
