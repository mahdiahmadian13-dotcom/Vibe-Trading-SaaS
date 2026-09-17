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
  min_workers: number; max_workers: number; autoscale_enabled: boolean;
  engine_healthy: boolean; engine_url_local: string | null;
  capability: Record<string, unknown> | null; capability_warning: boolean;
  provision_state: string | null; provision_step: string | null;
  tailscale_ip: string | null; node_role: string | null; has_ssh: boolean;
};

export const getServer = (id: number) => api<ServerRow & {
  workers: Array<{ name: string; status: string; last_seen: string | null }>;
  provision_log: Array<{ step: string; ts: string; ok: boolean | null; msg_fa: string }>;
  provision_job: { id: number; status: string; steps: ProvisionStep[] } | null;
}>(`/api/v1/admin/servers/${id}`);

export const manageServer = (id: number, body: { min_workers?: number; max_workers?: number; autoscale_enabled?: boolean; drain?: boolean }) =>
  api<{ ok: boolean; id: number; status: string }>(`/api/v1/admin/servers/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const listServers = () => api<ServerRow[]>("/api/v1/admin/servers");
export const createServer = (body: { name: string; region?: string | null; desired_workers?: number; worker_concurrency?: number; cpu_limit?: string; mem_limit?: string }) =>
  api<{ id: number; name: string; join_token: string }>("/api/v1/admin/servers", { method: "POST", body: JSON.stringify(body) });
export const deleteServer = (id: number) => api<{ ok: boolean }>(`/api/v1/admin/servers/${id}`, { method: "DELETE" });
export const scaleServer = (id: number, body: { desired_workers: number; worker_concurrency?: number; cpu_limit?: string; mem_limit?: string }) =>
  api<{ ok: boolean; desired_workers: number }>(`/api/v1/admin/servers/${id}/scale`, { method: "POST", body: JSON.stringify(body) });

// ---------------------------------------------------------------------------
// Auto-provision (full node over SSH — US9, FR-020)
// ---------------------------------------------------------------------------

export type ProvisionStep = { step: string; ts: string; ok: boolean | null; msg_fa: string };

export const getPreflight = (authType: "password" | "key" | "freestyle") =>
  api<import("../components/fleet/PreflightReport").PreflightReportData>(`/api/v1/admin/fleet/preflight?auth_type=${authType}`);

export const provisionServer = (body: {
  name: string; ssh_host: string; ssh_user?: string; auth_type: "password" | "key" | "freestyle";
  ssh_password?: string | null; ssh_key?: string | null; tailscale_ip?: string | null;
  region?: string | null; min_workers?: number; max_workers?: number;
}) => api<{ id: number; provision_job_id: number; status: string; current_step: string }>(
  "/api/v1/admin/fleet/servers", { method: "POST", body: JSON.stringify(body) });

export const getProvision = (serverId: number) =>
  api<{ status: string; current_step: string | null; steps: ProvisionStep[]; job_id: number }>(
    `/api/v1/admin/fleet/servers/${serverId}/provision`);

export const retryProvision = (serverId: number) =>
  api<{ ok: boolean; status: string; current_step: string | null }>(
    `/api/v1/admin/fleet/servers/${serverId}/provision/retry`, { method: "POST" });

// ---------------------------------------------------------------------------
// Fleet metrics (live dashboard — US10, FR-022)
// ---------------------------------------------------------------------------

export type FleetLive = {
  ts: string;
  fleet: { load: number; queue: number; workers: number; tasks_running: number };
  servers: Array<{ id: number; name: string; status: string; load: number; queue: number; workers: number; engine_healthy: boolean; capability_warning: boolean }>;
  central_load: number;
  tasks_live: Array<{ task_id: string; type: string; status: string | null; worker: string | null; server: string; elapsed_s: number | null }>;
};

export const getFleetLive = () => api<FleetLive>("/api/v1/admin/fleet/metrics/live");

export const getFleetHistory = (serverId: number, metric = "load", hours = 72) =>
  api<{ server_id: number; metric: string; hours: number; points: Array<[string, number]> }>(
    `/api/v1/admin/fleet/metrics/history?server_id=${serverId}&metric=${metric}&hours=${hours}`);

// ---------------------------------------------------------------------------
// Admin roles (US12, FR-024)
// ---------------------------------------------------------------------------

export type AdminRole = { id: number; name: string; perms: Record<string, boolean>; members: number };

export const listRoles = () => api<AdminRole[]>("/api/v1/admin/roles");
export const createRole = (body: { name: string; perms: Record<string, boolean> }) =>
  api<AdminRole>("/api/v1/admin/roles", { method: "POST", body: JSON.stringify(body) });
export const updateRole = (id: number, body: { name: string; perms: Record<string, boolean> }) =>
  api<{ ok: boolean }>(`/api/v1/admin/roles/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteRole = (id: number) =>
  api<{ ok: boolean }>(`/api/v1/admin/roles/${id}`, { method: "DELETE" });
export const assignRole = (user_id: number, role_id: number) =>
  api<{ ok: boolean }>("/api/v1/admin/roles/assign", { method: "POST", body: JSON.stringify({ user_id, role_id }) });


// ---------------------------------------------------------------------------
// Fleet update (one-click engine core update + worker rollout)
// ---------------------------------------------------------------------------

export type FleetUpdateJob = {
  id: number; status: string; step: string; scope: string;
  from_commit: string | null; to_commit: string | null; changed: boolean;
  log: Array<{ ts: string; line: string }>; error: string | null;
  per_node: Record<string, string>; cancel_requested: boolean;  // US11 staged rollout
  started_at: string | null; finished_at: string | null; created_at: string | null;
};

export type FleetUpdateStatus = {
  job: FleetUpdateJob | null;
  servers: Array<{ id: number; name: string; status: string; worker_epoch: number; workers_epoch_reported: number; converged: boolean; observed_workers: number }>;
};

export const triggerFleetUpdate = (include_platform: boolean) =>
  api<{ id: number; status: string; scope: string }>("/api/v1/admin/fleet/update", { method: "POST", body: JSON.stringify({ include_platform }) });
export const getFleetUpdateStatus = () => api<FleetUpdateStatus>("/api/v1/admin/fleet/update/status");
export const cancelFleetUpdate = (jobId: number) =>
  api<{ ok: boolean; status: string }>(`/api/v1/admin/fleet/update/${jobId}`, { method: "DELETE" });


  // ---------------------------------------------------------------------------
  // Rich monitoring (/admin/monitor/full)
  // ---------------------------------------------------------------------------
  export type MonitorFull = {
    tasks_1h: number; failed_1h: number; queue_depth: number | null;
    per_server: { id: number; name: string; region: string | null; status: string;
      desired_workers: number; online_workers: number; registered_workers: number;
      tasks_1h: number; failed_1h: number; cpu_limit: string | null; mem_limit: string | null;
      host: { cpu_count?: number; mem_total_gb?: number; disk_total_gb?: number; disk_used_pct?: number; hostname?: string };
      docker_ok: boolean; last_heartbeat_at: string | null }[];
    per_worker: { name: string; server: string; total: number; completed: number; failed: number;
      running: number; pending: number; avg_sec: number | null; success_pct: number }[];
    dispatcher: { workers: { name: string; status: string; concurrency: number;
      queue_depth: number; inflight: number; load: number }[];
      stats: Record<string, number>; fallback_depth: number };
    series_15min: { t: number; total: number; completed: number; failed: number }[];
    recent: { task_id: string; type: string; status: string | null; worker: string | null; server: string;
      user: number; created_at: string | null; duration_sec: number | null; error: string | null }[];
    ts: string;
  };
  export const getMonitorFull = () => api<MonitorFull>("/api/v1/admin/monitor/full");
