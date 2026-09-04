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

export const getRuns = () => api<RunRow[]>("/api/v1/vibe/runs");
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
