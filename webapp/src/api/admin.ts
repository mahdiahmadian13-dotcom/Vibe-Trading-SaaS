import { api } from "./client";

export type AdminOverview = {
  kpi: { total_users: number; active_subs: number; tasks_today: number; pending: number; running: number; by_type: Record<string, number> };
  engines: Array<{ id: number; name: string; url: string; is_healthy: boolean; is_enabled: boolean; active: number; max_concurrency: number; last_health_detail: string | null }>;
  workers: Array<{ name: string; status: string; last_seen_at: string | null }>;
  recent_logins: Array<{ id: number; user_id: number; ip: string | null; user_agent: string; created_at: string | null }>;
};

export type MonitorSummary = {
  tasks: { total: number; today: number; pending: number; running: number; completed: number; failed: number; queue_depth: number | null };
  engines: Array<{ id: number; name: string; is_healthy: boolean; is_enabled: boolean; active: number }>;
  workers: Array<{ name: string; status: string; last_seen_at: string | null }>;
};

export type AdminUserRow = {
  id: number; username: string; phone: string | null; telegram_id: number | null;
  is_active: boolean; is_admin: boolean; plan: string; plan_expires_at: string | null;
  created_at: string | null; last_login_at: string | null; last_login_ip: string | null;
};

export type EngineRow = {
  id: number; name: string; url: string; region: string | null;
  max_concurrency: number; active: number; is_enabled: boolean;
  is_healthy: boolean; fail_count: number; last_health_at: string | null; last_health_detail: string | null;
};

export type WorkerRow = { name: string; status: string; last_seen_at: string | null; info: unknown };

export const getOverview = () => api<AdminOverview>("/api/v1/admin/overview");
export const getMonitor = () => api<MonitorSummary>("/api/v1/admin/monitor/summary");
export const listUsers = (q?: string) => api<AdminUserRow[]>(`/api/v1/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
export const getUser = (id: number) => api<AdminUserRow & { subscriptions: Array<{ id: number; plan: string; status: string; expires_at: string | null }> }>(`/api/v1/admin/users/${id}`);
export const createUser = (body: { username: string; password: string; phone?: string | null; is_admin?: boolean; is_active?: boolean; plan_tier?: string; plan_days?: number }) =>
  api<{ id: number; username: string }>("/api/v1/admin/users", { method: "POST", body: JSON.stringify(body) });
export const updateUser = (id: number, body: Record<string, unknown>) =>
  api<{ ok: boolean }>(`/api/v1/admin/users/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteUser = (id: number) => api<{ ok: boolean }>(`/api/v1/admin/users/${id}`, { method: "DELETE" });
export const resetPassword = (id: number, new_password: string) =>
  api<{ ok: boolean }>(`/api/v1/admin/users/${id}/reset-password`, { method: "POST", body: JSON.stringify({ new_password }) });
export const grantPlan = (id: number, plan_tier: string, days: number) =>
  api<{ ok: boolean }>(`/api/v1/admin/users/${id}/grant`, { method: "POST", body: JSON.stringify({ plan_tier, days }) });
export const getLoginLogs = (user_id?: number) =>
  api<Array<{ id: number; user_id: number; ip: string | null; user_agent: string; created_at: string | null }>>(
    `/api/v1/admin/login-logs${user_id ? `?user_id=${user_id}` : ""}`
  );

export const listFleet = () => api<EngineRow[]>("/api/v1/admin/fleet");
export const addFleet = (body: { name: string; url: string; api_key?: string | null; region?: string | null; max_concurrency?: number; is_enabled?: boolean }) =>
  api<{ id: number; name: string; healthy: boolean; detail: string }>("/api/v1/admin/fleet", { method: "POST", body: JSON.stringify(body) });
export const updateFleet = (id: number, body: { name: string; url: string; api_key?: string | null; region?: string | null; max_concurrency?: number; is_enabled?: boolean }) =>
  api<{ ok: boolean }>(`/api/v1/admin/fleet/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteFleet = (id: number) => api<{ ok: boolean }>(`/api/v1/admin/fleet/${id}`, { method: "DELETE" });
export const recheckFleet = (id: number) => api<{ id: number; healthy: boolean; detail: string }>(`/api/v1/admin/fleet/${id}/health`, { method: "POST" });
export const listWorkers = () => api<WorkerRow[]>("/api/v1/admin/workers");


// ---------------------------------------------------------------------------
// Server fleet (one-line join + panel-controlled scaling)
// ---------------------------------------------------------------------------

export type ServerRow = {
  id: number; name: string; region: string | null; join_token: string;
  desired_workers: number; worker_concurrency: number;
  cpu_limit: string; mem_limit: string;
  status: string; observed_workers: number; online_workers: number; worker_names: string[];
  docker_ok: boolean; host_info: { hostname?: string; cpu_count?: number; mem_total_gb?: number; disk_total_gb?: number; os?: string } | null;
  last_heartbeat_at: string | null; created_at: string | null;
};

export const listServers = () => api<ServerRow[]>("/api/v1/admin/servers");
export const createServer = (body: { name: string; region?: string | null; desired_workers?: number; worker_concurrency?: number; cpu_limit?: string; mem_limit?: string }) =>
  api<{ id: number; name: string; join_token: string }>("/api/v1/admin/servers", { method: "POST", body: JSON.stringify(body) });
export const deleteServer = (id: number) => api<{ ok: boolean }>(`/api/v1/admin/servers/${id}`, { method: "DELETE" });
export const scaleServer = (id: number, body: { desired_workers: number; worker_concurrency?: number; cpu_limit?: string; mem_limit?: string }) =>
  api<{ ok: boolean; desired_workers: number }>(`/api/v1/admin/servers/${id}/scale`, { method: "POST", body: JSON.stringify(body) });
