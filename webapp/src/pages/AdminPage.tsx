  import { useCallback, useEffect, useRef, useState } from "react";
  import {
    Activity, BadgeCheck, Box, Copy, Eye, EyeOff, HardDrive, LayoutDashboard,
    LogIn, Minus, Monitor, Plus, RefreshCw, Search, Server, Shield, Terminal, Trash2, Users, Wrench, X,
  } from "lucide-react";
  import { api } from "@/api/client";
  import * as adminApi from "@/api/admin";
  import type { AdminOverview, AdminUserRow, EngineRow, MonitorSummary, ServerRow } from "@/api/admin";
  import { Button } from "@/components/ui/Button";
  import { Card } from "@/components/ui/primitives";
  import { FleetCards, LoadChart, TasksBar, DistDonut, HealthMap, LiveTasks } from "@/components/fleet/widgets";

  // ---------------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------------

  function useAdminGuard(onDeny: () => void) {
    const [ok, setOk] = useState<boolean | null>(null);
    useEffect(() => {
      api<{ plan: string; limits: Record<string, unknown> }>("/api/v1/subscription/current")
        .then(() => api<unknown[]>("/api/v1/admin/overview").then(() => setOk(true)).catch(() => { setOk(false); onDeny(); }))
        .catch(() => { setOk(false); onDeny(); });
    }, [onDeny]);
    return ok;
  }

  function fmtFa(d: string | null | undefined): string {
    if (!d) return "—";
    try { return new Date(d).toLocaleString("fa-IR", { dateStyle: "medium", timeStyle: "short" } as unknown as Intl.DateTimeFormatOptions); } catch { return d; }
  }

  function planBadge(plan: string) {
    const m: Record<string, { label: string; cls: string }> = {
      free: { label: "رایگان", cls: "bg-white/10 text-muted" },
      basic: { label: "پایه", cls: "bg-sky-500/15 text-sky-200 ring-sky-500/30" },
      pro: { label: "حرفه‌ای", cls: "bg-violet-500/15 text-violet-200 ring-violet-500/30" },
      enterprise: { label: "سازمانی", cls: "bg-amber-500/15 text-amber-200 ring-amber-500/30" },
    };
    const v = m[plan] ?? m.free;
    return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-bold ring-1 ring-inset ${v.cls}`}>{v.label}</span>;
  }

  function Toast({ msg, onClose }: { msg: string; onClose: () => void }) {
    useEffect(() => { const t = setTimeout(onClose, 2600); return () => clearTimeout(t); }, [onClose]);
    return <div className="fixed bottom-6 left-1/2 z-[80] -translate-x-1/2 rounded-xl border border-white/10 bg-zinc-900 px-4 py-2.5 text-sm text-zinc-100 shadow-xl">{msg}</div>;
  }

  function Confirm({ title, body, onConfirm, onCancel }: { title: string; body: string; onConfirm: () => void; onCancel: () => void }) {
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onCancel}>
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-zinc-900 p-5" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-sm font-extrabold">{title}</h3>
          <p className="mt-2 text-sm leading-6 text-zinc-400">{body}</p>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onCancel}>انصراف</Button>
            <Button variant="destructive" onClick={onConfirm}>تأیید</Button>
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Page
  // ---------------------------------------------------------------------------

  type Tab = "overview" | "users" | "nodes" | "live" | "logs";

  export default function AdminPage({ onHome }: { onHome: () => void }) {
    const [tab, setTab] = useState<Tab>("overview");
    const [toast, setToast] = useState("");
    const guardOk = useAdminGuard(onHome);

    if (guardOk === null) {
      return <div className="mx-auto max-w-6xl px-5 py-12 text-sm text-muted">در حال بررسی دسترسی ادمین…</div>;
    }
    if (guardOk === false) return null;

    const tabs: Array<{ id: Tab; label: string; icon: typeof LayoutDashboard }> = [
      { id: "overview", label: "نمای کلی", icon: LayoutDashboard },
      { id: "users", label: "کاربران", icon: Users },
      { id: "nodes", label: "نودها", icon: Server },
      { id: "live", label: "مانیتورینگ زنده", icon: Monitor },
      { id: "logs", label: "لاگ ورود", icon: LogIn },
    ];

    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-6" dir="rtl">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <h1 className="flex items-center gap-2 text-lg font-black"><Shield className="text-brand" size={18} /> پنل مدیریت</h1>
          <Button variant="ghost" onClick={onHome} className="gap-1.5 text-xs"><X size={14} /> خروج</Button>
        </div>

        <div className="mb-6 flex gap-1 overflow-auto rounded-xl border border-white/5 bg-black/20 p-1">
          {tabs.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 py-2 text-xs font-bold transition ${active ? "bg-brand text-white shadow" : "text-muted hover:bg-white/5 hover:text-zinc-200"}`}>
                <Icon size={13} /> {t.label}
              </button>
            );
          })}
        </div>

        {tab === "overview" && <OverviewTab onToast={setToast} />}
        {tab === "users" && <UsersTab onToast={setToast} />}
        {tab === "nodes" && <NodesTab onToast={setToast} />}
        {tab === "live" && <LiveTab />}
        {tab === "logs" && <LogsTab />}

        {toast && <Toast msg={toast} onClose={() => setToast("")} />}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Overview
  // ---------------------------------------------------------------------------

  function OverviewTab({ onToast }: { onToast: (m: string) => void }) {
    const [data, setData] = useState<AdminOverview | null>(null);
    const [err, setErr] = useState("");
    const load = useCallback(() => adminApi.getOverview().then(setData).catch((e: Error) => setErr(e.message)), []);
    useEffect(() => { load(); }, [load]);

    if (err) return <Card className="p-6 text-sm text-red-300">{err}</Card>;
    if (!data) return <Card className="p-6 text-sm text-muted">در حال بارگذاری…</Card>;

    const k = data.kpi;
    const cards = [
      { label: "کاربران", value: k.total_users, icon: Users },
      { label: "اشتراک فعال", value: k.active_subs, icon: BadgeCheck },
      { label: "تسک امروز", value: k.tasks_today, icon: Box },
      { label: "در انتظار / در حال اجرا", value: `${k.pending} / ${k.running}`, icon: Activity },
    ];

    return (
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {cards.map((c) => {
            const Icon = c.icon;
            return (
              <Card key={c.label} className="p-4">
                <div className="flex items-center gap-2 text-[11px] font-bold text-muted"><Icon size={13} /> {c.label}</div>
                <div className="mt-2 text-xl font-black tracking-tight">{c.value}</div>
              </Card>
            );
          })}
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Card className="p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-extrabold"><HardDrive size={14} className="text-muted" /> موتورها</div>
            {data.engines.length === 0 ? <p className="text-xs text-muted">موتوری ثبت نشده</p> : (
              <ul className="space-y-2">
                {data.engines.map((n) => (
                  <li key={n.id} className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[.02] px-3 py-2">
                    <span className="text-xs font-bold">{n.name}</span>
                    <span className="flex items-center gap-2 text-[11px]">
                      <span className={`h-2 w-2 rounded-full ${n.is_healthy ? "bg-emerald-400" : "bg-red-400"}`} />
                      <span className={n.is_healthy ? "text-emerald-300" : "text-red-300"}>{n.is_healthy ? "سالم" : "ناموفق"}</span>
                      <span className="text-muted">{n.active}/{n.max_concurrency}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card className="p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-extrabold"><Wrench size={14} className="text-muted" /> ورکرها</div>
            {data.workers.length === 0 ? <p className="text-xs text-muted">ورکری ثبت نشده</p> : (
              <ul className="space-y-2">
                {data.workers.map((w) => (
                  <li key={w.name} className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[.02] px-3 py-2">
                    <span className="text-xs font-bold">{w.name}</span>
                    <span className="text-[11px] text-muted">{w.status} · {fmtFa(w.last_seen_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <Card className="overflow-hidden">
          <div className="border-b border-white/5 px-4 py-3 text-xs font-extrabold">آخرین ورودها</div>
          <div className="overflow-auto">
            <table className="w-full min-w-[520px] text-xs">
              <thead className="bg-white/[.02] text-[11px] text-muted"><tr><th className="px-3 py-2 text-right">کاربر</th><th className="px-3 py-2 text-right">IP</th><th className="px-3 py-2 text-right">زمان</th></tr></thead>
              <tbody>
                {data.recent_logins.map((r) => (
                  <tr key={r.id} className="border-t border-white/5"><td className="px-3 py-2">{r.user_id}</td><td className="px-3 py-2 font-mono text-[11px]">{r.ip ?? "—"}</td><td className="px-3 py-2 text-muted">{fmtFa(r.created_at)}</td></tr>
                ))}
                {data.recent_logins.length === 0 && <tr><td colSpan={3} className="px-3 py-6 text-center text-muted">لاگی نیست</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Users
  // ---------------------------------------------------------------------------


  // Persian/Arabic digits → latin + int (Persian keyboard safe)
  const faDigits = "۰۱۲۳۴۵۶۷۸۹";
  const arDigits = "٠١٢٣٤٥٦٧٨٩";
  function parseNum(v: string): number {
    let s = "";
    for (const ch of v) {
      const fi = faDigits.indexOf(ch);
      const ai = arDigits.indexOf(ch);
      if (fi >= 0) s += String(fi);
      else if (ai >= 0) s += String(ai);
      else s += ch;
    }
    s = s.replace(/[^0-9]/g, "");
    return s ? parseInt(s, 10) : 0;
  }

  function UsersTab({ onToast }: { onToast: (m: string) => void }) {
    const [q, setQ] = useState("");
    const [rows, setRows] = useState<AdminUserRow[] | null>(null);
    const [err, setErr] = useState("");
    const [createOpen, setCreateOpen] = useState(false);
    const [editRow, setEditRow] = useState<AdminUserRow | null>(null);
    const [rolesOpen, setRolesOpen] = useState(false);
    const [grantRow, setGrantRow] = useState<AdminUserRow | null>(null);
    const [pwdRow, setPwdRow] = useState<AdminUserRow | null>(null);
    const [pendingDelete, setPendingDelete] = useState<AdminUserRow | null>(null);

    const load = useCallback(() => {
      setErr("");
      adminApi.listUsers(q.trim() || undefined).then(setRows).catch((e: Error) => setErr(e.message));
    }, [q]);
    useEffect(() => { load(); }, [load]);

    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-white/10 bg-white/[.03] px-3 py-2">
            <Search size={14} className="text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="جستجو نام کاربری / موبایل…" className="w-full bg-transparent text-sm outline-none placeholder:text-muted" />
          </div>
          <Button onClick={() => setCreateOpen(true)} className="gap-1.5"><Plus size={14} /> کاربر جدید</Button>
          <Button variant="outline" onClick={() => setRolesOpen(true)} className="gap-1.5"><Shield size={14} /> نقش‌ها</Button>
          <Button variant="outline" onClick={load} className="gap-1.5"><RefreshCw size={14} /> بروزرسانی</Button>
        </div>

        {err && <Card className="p-3 text-sm text-red-300">{err}</Card>}

        <Card className="overflow-hidden">
          <div className="overflow-auto">
            <table className="w-full min-w-[760px] text-xs">
              <thead className="bg-white/[.04] text-[11px] font-bold text-muted">
                <tr><th className="px-3 py-2.5 text-right">کاربر</th><th className="px-3 py-2.5 text-right">پلن / انقضا</th><th className="px-3 py-2.5 text-right">ایجاد</th><th className="px-3 py-2.5 text-right">آخرین ورود</th><th className="px-3 py-2.5 text-right">وضعیت</th><th className="px-3 py-2.5 text-right">عملیات</th></tr>
              </thead>
              <tbody>
                {!rows ? <tr><td colSpan={6} className="px-3 py-8 text-center text-muted">در حال بارگذاری…</td></tr>
                  : rows.length === 0 ? <tr><td colSpan={6} className="px-3 py-8 text-center text-muted">موردی یافت نشد</td></tr>
                    : rows.map((u) => (
                      <tr key={u.id} className="border-t border-white/5 hover:bg-white/[.02]">
                        <td className="px-3 py-2.5"><span className="font-bold">{u.username}</span>{u.is_admin && <span className="mr-1.5 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-200">ادمین</span>}<div className="text-[11px] text-muted">{u.phone ?? "—"} {u.telegram_id ? `· tg:${u.telegram_id}` : ""}</div></td>
                        <td className="px-3 py-2.5"><div className="flex items-center gap-1.5">{planBadge(u.plan)}<span className="text-[11px] text-muted">{fmtFa(u.plan_expires_at)}</span></div></td>
                        <td className="px-3 py-2.5 text-muted">{fmtFa(u.created_at)}</td>
                        <td className="px-3 py-2.5 text-muted">{u.last_login_at ? <><span className="font-mono text-[11px]">{u.last_login_ip ?? ""}</span> <span className="text-[11px]">{fmtFa(u.last_login_at)}</span></> : "—"}</td>
                        <td className="px-3 py-2.5">{u.is_active ? <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-bold text-emerald-200">فعال</span> : <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-bold text-red-200">غیرفعال</span>}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap gap-1">
                            <Button size="sm" variant="outline" onClick={() => setEditRow(u)}>ویرایش</Button>
                            <Button size="sm" variant="outline" onClick={() => setGrantRow(u)}>پلن</Button>
                            <Button size="sm" variant="ghost" onClick={() => setPwdRow(u)}><Eye size={12} /> رمز</Button>
                            <Button size="sm" variant="ghost" onClick={() => setPendingDelete(u)} className="text-red-300"><Trash2 size={12} /></Button>
                          </div>
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        </Card>

        {createOpen && <CreateUserModal onClose={() => setCreateOpen(false)} onDone={() => { setCreateOpen(false); onToast("کاربر ساخته شد"); load(); }} />}
        {editRow && <EditUserModal row={editRow} onClose={() => setEditRow(null)} onDone={() => { setEditRow(null); onToast("ذخیره شد"); load(); }} />}
        {grantRow && <GrantModal row={grantRow} onClose={() => setGrantRow(null)} onDone={() => { setGrantRow(null); onToast("پلن اعمال شد"); load(); }} />}
        {pwdRow && <ResetPwdModal row={pwdRow} onClose={() => setPwdRow(null)} onDone={() => { setPwdRow(null); onToast("رمز تغییر کرد"); }} />}
        {pendingDelete && <Confirm title="حذف کاربر" body={`آیا ${pendingDelete.username} حذف شود؟ تمام اشتراک‌ها و تسک‌های او پاک می‌شود.`} onCancel={() => setPendingDelete(null)} onConfirm={async () => { try { await adminApi.deleteUser(pendingDelete.id); onToast("حذف شد"); } catch (e) { onToast((e as Error).message); } setPendingDelete(null); load(); }} />}
        {rolesOpen && <RolesModal onClose={() => setRolesOpen(false)} onToast={onToast} />}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Roles modal (US-12): definable roles + permission flags + member assign
  // ---------------------------------------------------------------------------
  const PERM_LABELS: Record<string, string> = { dashboard: "مشاهده داشبورد", servers: "مدیریت سرور", users: "مدیریت کاربر", secrets: "دیدن سکرت", updates: "آپدیت ناوگان" };
  function RolesModal({ onClose, onToast }: { onClose: () => void; onToast: (m: string) => void }) {
    const [roles, setRoles] = useState<import("@/api/admin").AdminRole[] | null>(null);
    const [err, setErr] = useState("");
    const [name, setName] = useState("");
    const [perms, setPerms] = useState<Record<string, boolean>>({ dashboard: true });
    const [busy, setBusy] = useState(false);
    const load = () => adminApi.listRoles().then(setRoles).catch((e: Error) => setErr(e.message));
    useEffect(() => { load(); }, []);
    const create = async () => {
      if (!name.trim()) { setErr("نام نقش الزامی است"); return; }
      setBusy(true); setErr("");
      try { await adminApi.createRole({ name: name.trim(), perms }); setName(""); setPerms({ dashboard: true }); load(); onToast("نقش ساخته شد"); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    const toggle = async (r: import("@/api/admin").AdminRole, key: string) => {
      try { await adminApi.updateRole(r.id, { name: r.name, perms: { ...r.perms, [key]: !r.perms[key] } }); load(); }
      catch (e) { onToast((e as Error).message); }
    };
    const remove = async (id: number) => {
      try { await adminApi.deleteRole(id); load(); onToast("نقش حذف شد"); }
      catch (e) { onToast((e as Error).message); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">نقش‌های مدیریتی</h3>
          <p className="mt-1 text-xs text-muted">نقش بسازید و دسترسی‌ها را تیک بزنید. امنیت در سمت سرور هم اعمال می‌شود (403 واقعی).</p>
          {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
          <div className="mt-4 space-y-2">
            {roles === null ? <p className="text-xs text-muted">در حال بارگذاری…</p> : roles.length === 0 ? <p className="text-xs text-muted">نقشی ساخته نشده</p> : roles.map((r) => (
              <div key={r.id} className="rounded-xl border border-white/10 p-3">
                <div className="flex items-center justify-between">
                  <span className="font-black">{r.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-muted">{r.members} عضو</span>
                    <button onClick={() => remove(r.id)} className="text-red-300"><Trash2 size={12} /></button>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.keys(PERM_LABELS).map((k) => (
                    <button key={k} onClick={() => toggle(r, k)} className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${r.perms[k] ? "bg-emerald-500/15 text-emerald-200" : "bg-white/10 text-muted"}`}>{PERM_LABELS[k]}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 rounded-xl border border-white/10 p-3">
            <h4 className="text-xs font-black text-muted">نقش جدید</h4>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="نام نقش (مثلاً اپراتور)" className="mt-2 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.keys(PERM_LABELS).map((k) => (
                <button key={k} onClick={() => setPerms({ ...perms, [k]: !perms[k] })} className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${perms[k] ? "bg-emerald-500/15 text-emerald-200" : "bg-white/10 text-muted"}`}>{PERM_LABELS[k]}</button>
              ))}
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>بستن</Button>
              <Button onClick={create} disabled={busy}>ساخت نقش</Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function CreateUserModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const [f, setF] = useState({ username: "", password: "", phone: "", is_admin: false, plan_tier: "pro", plan_days: 30 });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const submit = async () => {
      if (!f.username.trim() || f.password.length < 6) { setErr("نام کاربری و رمز (حداقل ۶ کاراکتر) الزامی است"); return; }
      setBusy(true); setErr("");
      try { await adminApi.createUser({ username: f.username.trim(), password: f.password, phone: f.phone.trim() || null, is_admin: f.is_admin, plan_tier: f.plan_tier, plan_days: f.plan_days }); onDone(); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">کاربر جدید</h3>
          {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
          <div className="mt-4 space-y-3">
            <input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="نام کاربری *" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <input value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} placeholder="رمز عبور *" type="password" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="موبایل (اختیاری)" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <div className="flex gap-2">
              <select value={f.plan_tier} onChange={(e) => setF({ ...f, plan_tier: e.target.value })} className="flex-1 rounded-xl border border-white/10 bg-zinc-800 px-3 py-2.5">
                <option value="free">رایگان</option><option value="basic">پایه</option><option value="pro">حرفه‌ای</option><option value="enterprise">سازمانی</option>
              </select>
              <input inputMode="numeric" value={f.plan_days} onChange={(e) => setF({ ...f, plan_days: parseNum(e.target.value) })} className="w-28 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5" />
            </div>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.is_admin} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} /> ادمین</label>
          </div>
          <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : "ایجاد"}</Button></div>
        </div>
      </div>
    );
  }

  function EditUserModal({ row, onClose, onDone }: { row: AdminUserRow; onClose: () => void; onDone: () => void }) {
    const [f, setF] = useState({ username: row.username, phone: row.phone ?? "", is_active: row.is_active, is_admin: row.is_admin });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const submit = async () => {
      setBusy(true); setErr("");
      try { await adminApi.updateUser(row.id, { username: f.username.trim(), phone: f.phone.trim() || null, is_active: f.is_active, is_admin: f.is_admin }); onDone(); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">ویرایش {row.username}</h3>
          {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
          <div className="mt-4 space-y-3">
            <input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="موبایل" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> فعال</label>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.is_admin} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} /> ادمین</label>
          </div>
          <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : "ذخیره"}</Button></div>
        </div>
      </div>
    );
  }

  function GrantModal({ row, onClose, onDone }: { row: AdminUserRow; onClose: () => void; onDone: () => void }) {
    const [tier, setTier] = useState("pro");
    const [days, setDays] = useState(30);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const submit = async () => {
      setBusy(true); setErr("");
      try { await adminApi.grantPlan(row.id, tier, days); onDone(); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">اعطای پلن به {row.username}</h3>
          <p className="mt-1 text-xs text-muted">فعلی: {row.plan} · انقضا {fmtFa(row.plan_expires_at)}</p>
          {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
          <div className="mt-4 flex gap-2">
            <select value={tier} onChange={(e) => setTier(e.target.value)} className="flex-1 rounded-xl border border-white/10 bg-zinc-800 px-3 py-2.5">
              <option value="basic">پایه</option><option value="pro">حرفه‌ای</option><option value="enterprise">سازمانی</option>
            </select>
            <input inputMode="numeric" value={days} onChange={(e) => setDays(parseNum(e.target.value))} className="w-28 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5" />
          </div>
          <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : "اعطا"}</Button></div>
        </div>
      </div>
    );
  }

  function ResetPwdModal({ row, onClose, onDone }: { row: AdminUserRow; onClose: () => void; onDone: () => void }) {
    const [pwd, setPwd] = useState("");
    const [show, setShow] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const submit = async () => {
      if (pwd.length < 6) { setErr("حداقل ۶ کاراکتر"); return; }
      setBusy(true); setErr("");
      try { await adminApi.resetPassword(row.id, pwd); onDone(); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">تغییر رمز {row.username}</h3>
          {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
          <div className="mt-4 flex gap-2">
            <input value={pwd} onChange={(e) => setPwd(e.target.value)} type={show ? "text" : "password"} placeholder="رمز جدید" className="flex-1 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <button onClick={() => setShow((v) => !v)} className="rounded-xl border border-white/10 px-3">{show ? <EyeOff size={14} /> : <Eye size={14} />}</button>
          </div>
          <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : "ذخیره"}</Button></div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Nodes
  // ---------------------------------------------------------------------------

  function NodesTab({ onToast }: { onToast: (m: string) => void }) {
    const [fleet, setFleet] = useState<EngineRow[] | null>(null);
    const [workers, setWorkers] = useState<import("@/api/admin").WorkerRow[] | null>(null);
    const [servers, setServers] = useState<ServerRow[] | null>(null);
    const [scaling, setScaling] = useState<number | null>(null);
    const [joinOpen, setJoinOpen] = useState(false);
    const [joinShow, setJoinShow] = useState<ServerRow | null>(null);
    const [serverDetail, setServerDetail] = useState<number | null>(null);
    const [pendingServerDelete, setPendingServerDelete] = useState<number | null>(null);
    const [err, setErr] = useState("");
    const [addOpen, setAddOpen] = useState(false);
    const [editNode, setEditNode] = useState<EngineRow | null>(null);
    const [pendingDelete, setPendingDelete] = useState<number | null>(null);
    const [updStatus, setUpdStatus] = useState<import("@/api/admin").FleetUpdateStatus | null>(null);
    const [updBusy, setUpdBusy] = useState(false);
    const [updConfirm, setUpdConfirm] = useState(false);

    const load = useCallback(() => {
      adminApi.listFleet().then(setFleet).catch((e: Error) => setErr(e.message));
      adminApi.listWorkers().then(setWorkers).catch(() => setWorkers([]));
      adminApi.listServers().then(setServers).catch(() => setServers([]));
      adminApi.getFleetUpdateStatus().then(setUpdStatus).catch(() => setUpdStatus(null));
    }, []);
    useEffect(() => { load(); }, [load]);

    // live progress while an update job is pending/running
    useEffect(() => {
      const st = updStatus?.job?.status;
      if (st !== "pending" && st !== "running") return;
      const t = setInterval(() => { adminApi.getFleetUpdateStatus().then(setUpdStatus).catch(() => {}); }, 3000);
      return () => clearInterval(t);
    }, [updStatus?.job?.status]);

    const triggerUpdate = async (include_platform: boolean) => {
      setUpdBusy(true); setUpdConfirm(false);
      try {
        await adminApi.triggerFleetUpdate(include_platform);
        onToast("آپدیت هسته شروع شد");
        adminApi.getFleetUpdateStatus().then(setUpdStatus).catch(() => {});
      } catch (e) { onToast((e as Error).message); }
      finally { setUpdBusy(false); }
    };

    const changeScale = async (s: ServerRow, delta: number) => {
      const target = Math.max(0, s.desired_workers + delta);
      setScaling(s.id);
      try { await adminApi.scaleServer(s.id, { desired_workers: target }); onToast(target === 0 ? "ورکرها صفر شدند" : `درخواست شد: ${target} ورکر`); load(); }
      catch (e) { onToast((e as Error).message); }
      finally { setScaling(null); }
    };

    const drainServer = async (id: number, drain: boolean) => {
      try { const r = await adminApi.manageServer(id, { drain }); onToast(drain ? "سرور به حالت تخلیه رفت — ورودی جدید قطع شد" : `سرور به چرخه برگشت (${r.status})`); load(); }
      catch (e) { onToast((e as Error).message); }
    };

    const copySnippet = async () => {
      const snippet = `BROKER_URL=redis://CENTRAL_IP:6379 \
ENGINE_URL=http://CENTRAL_IP:8899 \
ENGINE_API_KEY=\$VIBE_ENGINE_API_KEY \
DATABASE_URL=postgresql+asyncpg://vt:PASS@CENTRAL_IP:5432/vibetrader \
WORKER_NAME=worker-eu-1 \
docker compose -f docker-compose.worker.yml up -d --build`;
      await navigator.clipboard.writeText(snippet);
      onToast("snippet کپی شد");
    };

    return (
      <div className="space-y-4">
        {err && <Card className="p-3 text-sm text-red-300">{err}</Card>}

        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-black"><RefreshCw size={14} className="text-brand" /> بروزرسانی هسته Vibe-Trading</h3>
            {(() => {
              const job = updStatus?.job;
              const running = job?.status === "pending" || job?.status === "running";
              return (
                <div className="flex gap-2">
                  <Button variant="ghost" size="sm" onClick={load}><RefreshCw size={12} /></Button>
                  <Button variant="outline" size="sm" disabled={updBusy || running} onClick={() => setUpdConfirm(true)} className="gap-1.5">
                    <RefreshCw size={13} /> بروزرسانی همه ورکرها
                  </Button>
                  {running && (
                    <Button variant="ghost" size="sm" onClick={async () => { try { await adminApi.cancelFleetUpdate(job!.id); onToast("درخواست لغو ثبت شد — بین دو گره متوقف می‌شود"); } catch (e) { onToast((e as Error).message); } }} className="gap-1.5 text-red-300">
                      <X size={13} /> لغو آپدیت
                    </Button>
                  )}
                </div>
              );
            })()}
          </div>

          {updConfirm && (
            <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-6">
              هسته از <code className="rounded bg-black/30 px-1">github.com/HKUDS/Vibe-Trading</code> fetch می‌شود، کامیت‌های محلی روی نسخه جدید rebase می‌شوند، ایمیج موتور rebuild و کانتینر recreate می‌شود. اگر بعد از آپدیت موتور سالم نباشد، خودکار به کامیت قبلی برمی‌گردد (rollback).
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setUpdConfirm(false)}>انصراف</Button>
                <Button size="sm" onClick={() => triggerUpdate(false)}>فقط هسته</Button>
                <Button size="sm" onClick={() => triggerUpdate(true)}>هسته + پلتفرم</Button>
              </div>
            </div>
          )}

          {(() => {
            const job = updStatus?.job;
            if (!job) return <p className="text-xs text-muted">هیچ آپدیتی تا الان اجرا نشده. با دکمه بالا، هسته و همه ورکرهای همه سرورها را یک‌جا به آخرین نسخه آپدیت کنید.</p>;
            const badge: Record<string, { label: string; cls: string }> = {
              pending: { label: "در صف", cls: "bg-amber-500/15 text-amber-200 ring-amber-500/30" },
              running: { label: `در حال اجرا — ${job.step}`, cls: "bg-sky-500/15 text-sky-200 ring-sky-500/30" },
              success: { label: "موفق", cls: "bg-emerald-500/15 text-emerald-200 ring-emerald-500/30" },
              up_to_date: { label: "به‌روز بود", cls: "bg-white/10 text-muted" },
              failed: { label: "ناموفق", cls: "bg-red-500/15 text-red-200 ring-red-500/30" },
              rolled_back: { label: "برگشت خودکار", cls: "bg-amber-500/15 text-amber-200 ring-amber-500/30" },
            };
            const b = badge[job.status] ?? badge.pending;
            return (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-bold ring-1 ring-inset ${b.cls}`}>{b.label}</span>
                  <span className="text-muted">#{job.id}</span>
                  {job.from_commit && <span className="text-muted" dir="ltr">{job.from_commit.slice(0, 9)} → {(job.to_commit ?? "?").slice(0, 9)}</span>}
                  <span className="text-muted">{fmtFa(job.finished_at ?? job.started_at ?? job.created_at)}</span>
                  {job.scope === "engine+platform" && <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px]">هسته+پلتفرم</span>}
                </div>
                {job.error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-[11px] leading-5 text-red-200" dir="auto">{job.error}</p>}
                {(job.log ?? []).length > 0 && (
                  <div dir="ltr" className="max-h-44 overflow-auto rounded-xl border border-white/10 bg-black/50 p-3 text-left font-mono text-[11px] leading-5 text-emerald-200">
                    {(job.log ?? []).map((l, i) => <div key={i}><span className="text-muted">{(l.ts ?? "").slice(11, 19)}</span> {l.line}</div>)}
                  </div>
                )}
                {Object.keys(job.per_node ?? {}).length > 0 && (
                  <div className="rounded-xl border border-white/10 p-3">
                    <div className="mb-2 text-[11px] font-black text-muted">انتشار مرحله‌ای گره‌ها (یکی‌یکی — حداکثر یک گره هم‌زمان)</div>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(job.per_node ?? {}).map(([name, st]) => (
                        <span key={name} className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[11px] font-bold ${st === "ok" ? "bg-emerald-500/10 text-emerald-200" : st === "rolled_back" || st === "failed" ? "bg-red-500/10 text-red-200" : st === "cancelled" ? "bg-white/10 text-muted" : "bg-sky-500/10 text-sky-200"}`}>
                          {st === "ok" ? "✓" : st === "rolled_back" || st === "failed" ? "✗" : "⏳"} {name}: {st === "ok" ? "به‌روز" : st === "draining" ? "تخلیه" : st === "updating" ? "در حال آپدیت" : st === "rolled_back" ? "برگشت + توقف" : st === "failed" ? "ناموفق + توقف" : st === "cancelled" ? "لغو شد" : st}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {(updStatus?.servers ?? []).length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {(updStatus?.servers ?? []).map((s) => (
                      <span key={s.id} className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[11px] font-bold ${s.converged ? "bg-emerald-500/10 text-emerald-200" : "bg-amber-500/10 text-amber-200"}`}>
                        {s.converged ? "✓" : "⏳"} {s.name}: {s.converged ? "ورکرها به‌روز" : `در انتظار همگرایی (epoch ${s.workers_epoch_reported}/${s.worker_epoch})`}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
        </Card>

        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-black"><Terminal size={14} className="text-brand" /> سرورها (Servers)</h3>
            <div className="flex gap-2">
              <Button onClick={() => setJoinOpen(true)} className="gap-1.5"><Plus size={14} /> افزودن سرور</Button>
              <Button variant="ghost" onClick={load}><RefreshCw size={14} /></Button>
            </div>
          </div>
          {!servers ? <p className="text-xs text-muted">در حال بارگذاری…</p> : servers.length === 0 ? <p className="text-xs text-muted">سروری اضافه نشده — با «افزودن سرور» یک دستور یک‌خطی بگیرید.</p> : (
            <div className="overflow-auto">
              <table className="w-full min-w-[820px] text-xs">
                <thead className="bg-white/[.04] text-[11px] text-muted"><tr><th className="px-3 py-2 text-right">سرور</th><th className="px-3 py-2 text-right">وضعیت</th><th className="px-3 py-2 text-right">ورکر</th><th className="px-3 py-2 text-right">سقف/خودکار</th><th className="px-3 py-2 text-right">انجین/توان</th><th className="px-3 py-2 text-right">heartbeat</th><th className="px-3 py-2 text-right">عملیات</th></tr></thead>
                <tbody>
                  {servers.map((s) => (
                    <tr key={s.id} className="border-t border-white/5">
                      <td className="px-3 py-2.5"><button onClick={() => setServerDetail(s.id)} className="font-bold hover:text-brand">{s.name}</button>{s.region && <span className="mr-1.5 rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted">{s.region}</span>}<div className="text-[10px] text-muted">{s.host_info?.hostname ?? "—"} · {s.host_info?.cpu_count ?? "?"}vCPU · {s.host_info?.mem_total_gb ?? "?"}GB</div></td>
                      <td className="px-3 py-2.5"><span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${s.status === "online" ? "bg-emerald-500/15 text-emerald-200" : s.status === "pending" ? "bg-amber-500/15 text-amber-200" : s.status === "draining" ? "bg-sky-500/15 text-sky-200" : "bg-red-500/15 text-red-200"}`}>{s.status === "online" ? "آنلاین" : s.status === "pending" ? "در انتظار نصب" : s.status === "draining" ? "در حال تخلیه" : s.status}</span></td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <button disabled={scaling === s.id} onClick={() => changeScale(s, -1)} className="rounded-md border border-white/10 p-1 text-muted hover:bg-white/10 disabled:opacity-40"><Minus size={11} /></button>
                          <span className={`min-w-[46px] text-center font-black ${s.online_workers !== s.desired_workers ? "text-amber-300" : "text-emerald-300"}`}>{s.online_workers}/{s.desired_workers}</span>
                          <button disabled={scaling === s.id} onClick={() => changeScale(s, 1)} className="rounded-md border border-white/10 p-1 text-muted hover:bg-white/10 disabled:opacity-40"><Plus size={11} /></button>
                        </div>
                        <div className="mt-1 text-[10px] text-muted">همزمانی: {s.worker_concurrency} · {s.worker_names.slice(0, 2).join(", ")}{s.worker_names.length > 2 ? ` +${s.worker_names.length - 2}` : ""}</div>
                      </td>
                      <td className="px-3 py-2.5 text-muted"><span dir="ltr">{s.min_workers}–{s.max_workers}</span>{s.autoscale_enabled ? <span className="mr-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-200">خودکار</span> : <span className="mr-1 rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted">دستی</span>}</td>
                      <td className="px-3 py-2.5 text-muted">
                        <span className={s.engine_healthy ? "text-emerald-300" : "text-red-300"}>{s.engine_healthy ? "سالم" : "خراب"}</span>
                        {s.capability_warning && <span className="mr-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-200">سرور ضعیف</span>}
                      </td>
                      <td className="px-3 py-2.5 text-muted">{fmtFa(s.last_heartbeat_at)}</td>
                      <td className="px-3 py-2.5">
                        <div className="flex gap-1">
                          <Button size="sm" variant="outline" onClick={() => setServerDetail(s.id)}>جزئیات</Button>
                          {s.status === "draining"
                            ? <Button size="sm" variant="outline" onClick={() => drainServer(s.id, false)}>بازگردانی</Button>
                            : <Button size="sm" variant="outline" onClick={() => drainServer(s.id, true)}>تخلیه</Button>}
                          <Button size="sm" variant="ghost" onClick={() => setPendingServerDelete(s.id)} className="text-red-300"><Trash2 size={12} /></Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-[11px] leading-5 text-muted">ورکرهای هر سرور را همین‌جا با +/− تنظیم کنید — ایجنت روی سرور ظرف ~۱۰ ثانیه اعمال می‌کند. سقف و حالت خودکار/دستی و تخلیه از همین جدول؛ جزئیات کامل (تست توان، لاگ نصب، ورکرها) با کلیک روی نام سرور.</p>
        </Card>
        {serverDetail !== null && <ServerDetailModal serverId={serverDetail} onClose={() => { setServerDetail(null); load(); }} onToast={onToast} />}

        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-black"><Server size={14} /> موتورها (Engine Nodes)</h3>
            <div className="flex gap-2">
              <Button variant="outline" onClick={copySnippet} className="gap-1.5 text-xs"><Copy size={12} /> snippet ورکر راه دور</Button>
              <Button onClick={() => setAddOpen(true)} className="gap-1.5"><Plus size={14} /> افزودن موتور</Button>
              <Button variant="ghost" onClick={load}><RefreshCw size={14} /></Button>
            </div>
          </div>
          {!fleet ? <p className="text-xs text-muted">در حال بارگذاری…</p> : fleet.length === 0 ? <p className="text-xs text-muted">موتوری ثبت نشده — موتور پیش‌فرض در بک‌اند ساخته می‌شود.</p> : (
            <div className="overflow-auto">
              <table className="w-full min-w-[720px] text-xs">
                <thead className="bg-white/[.04] text-[11px] text-muted"><tr><th className="px-3 py-2 text-right">نام</th><th className="px-3 py-2 text-right">URL</th><th className="px-3 py-2 text-right">سلامت</th><th className="px-3 py-2 text-right">ظرفیت</th><th className="px-3 py-2 text-right">فعال</th><th className="px-3 py-2 text-right">عملیات</th></tr></thead>
                <tbody>
                  {fleet.map((n) => (
                    <tr key={n.id} className="border-t border-white/5">
                      <td className="px-3 py-2.5 font-bold">{n.name} {n.region && <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted">{n.region}</span>}</td>
                      <td className="px-3 py-2.5 font-mono text-[11px]">{n.url}</td>
                      <td className="px-3 py-2.5"><span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${n.is_healthy ? "bg-emerald-500/15 text-emerald-200" : "bg-red-500/15 text-red-200"}`}>{n.is_healthy ? "سالم" : "خطا"} {n.fail_count ? `(${n.fail_count})` : ""}</span><div className="max-w-[220px] truncate text-[10px] text-muted">{n.last_health_detail ?? ""}</div></td>
                      <td className="px-3 py-2.5">{n.active}/{n.max_concurrency}</td>
                      <td className="px-3 py-2.5">{n.is_enabled ? "فعال" : "غیرفعال"}</td>
                      <td className="px-3 py-2.5">
                        <div className="flex gap-1">
                          <Button size="sm" variant="outline" onClick={async () => { try { await adminApi.recheckFleet(n.id); onToast("بررسی شد"); load(); } catch (e) { onToast((e as Error).message); } }}>بررسی</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditNode(n)}>ویرایش</Button>
                          <Button size="sm" variant="ghost" onClick={() => setPendingDelete(n.id)} className="text-red-300"><Trash2 size={12} /></Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card className="p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-black"><Wrench size={14} /> ورکرها</h3>
          {!workers ? <p className="text-xs text-muted">در حال بارگذاری…</p> : workers.length === 0 ? <p className="text-xs text-muted">ورکری ثبت نشده</p> : (
            <div className="overflow-auto">
              <table className="w-full min-w-[520px] text-xs">
                <thead className="bg-white/[.04] text-[11px] text-muted"><tr><th className="px-3 py-2 text-right">نام</th><th className="px-3 py-2 text-right">وضعیت</th><th className="px-3 py-2 text-right">آخرین حضور</th></tr></thead>
                <tbody>
                  {workers.map((w) => (
                    <tr key={w.name} className="border-t border-white/5"><td className="px-3 py-2.5 font-bold">{w.name}</td><td className="px-3 py-2.5">{w.status}</td><td className="px-3 py-2.5 text-muted">{fmtFa(w.last_seen_at)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-[11px] leading-5 text-muted">برای افزودن ورکر جدید روی سرور دیگر، متغیرهای <code className="rounded bg-white/10 px-1 font-mono">BROKER_URL / ENGINE_URL / DATABASE_URL</code> را ست کنید و <code className="rounded bg-white/10 px-1 font-mono">docker compose -f docker-compose.worker.yml up -d</code> را اجرا کنید.</p>
        </Card>

        {addOpen && <FleetModal onClose={() => setAddOpen(false)} onDone={() => { setAddOpen(false); load(); onToast("موتور افزوده شد"); }} />}
        {editNode && <FleetModal node={editNode} onClose={() => setEditNode(null)} onDone={() => { setEditNode(null); load(); onToast("ذخیره شد"); }} />}
        {pendingDelete !== null && <Confirm title="حذف موتور" body="این موتور از fleet حذف شود؟ تسک‌های در حال اجرا قطع نمی‌شوند." onCancel={() => setPendingDelete(null)} onConfirm={async () => { try { await adminApi.deleteFleet(pendingDelete); onToast("حذف شد"); } catch (e) { onToast((e as Error).message); } setPendingDelete(null); load(); }} />}

        {joinOpen && <ProvisionModal onClose={() => setJoinOpen(false)} onDone={() => { setJoinOpen(false); load(); }} />}
        {joinShow && <JoinShowModal server={joinShow} onClose={() => setJoinShow(null)} onToast={onToast} />}
        {pendingServerDelete !== null && <Confirm title="حذف سرور" body="سرور از پنل حذف شود؟ ورکرهایش باید ابتدا به 0 تنظیم شوند." onCancel={() => setPendingServerDelete(null)} onConfirm={async () => { try { await adminApi.deleteServer(pendingServerDelete); onToast("حذف شد"); } catch (e) { onToast((e as Error).message); } setPendingServerDelete(null); load(); }} />}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Auto-provision modal (US-09, FR-020): IP + SSH credential → full node,
  // live stepwise progress + retry-from-failed-step. Manual join flow lives
  // in JoinServerModal below (kept for SSH-less servers).
  // ---------------------------------------------------------------------------
  function ProvisionModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const [f, setF] = useState({ name: "", ssh_host: "", ssh_user: "root", auth_type: "password" as "password" | "key" | "freestyle", secret: "", tailscale_ip: "", region: "", min_workers: 1, max_workers: 4 });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const [prov, setProv] = useState<{ serverId: number; status: string; current_step: string | null; steps: import("@/api/admin").ProvisionStep[] } | null>(null);

    const poll = async (serverId: number) => {
      try {
        const p = await adminApi.getProvision(serverId);
        setProv({ serverId, status: p.status, current_step: p.current_step, steps: p.steps });
        return p.status;
      } catch { return "unknown"; }
    };

    useEffect(() => {
      if (!prov || prov.status !== "running") return;
      const t = setInterval(async () => {
        const st = await poll(prov.serverId);
        if (st !== "running") clearInterval(t);
      }, 3000);
      return () => clearInterval(t);
    }, [prov?.serverId, prov?.status]);

    const submit = async () => {
      if (!f.name.trim()) { setErr("نام سرور الزامی است (مثلاً srv-eu-1)"); return; }
      if (!f.ssh_host.trim()) { setErr("IP/هاست SSH یا شناسه VM الزامی است"); return; }
      if (f.auth_type === "freestyle") {
        // whole `npx freestyle vm ssh ...` line is accepted; gateway extracts the vm id
        if (!/vm-|freestyle/i.test(f.ssh_host) && !/^vm-/.test(f.ssh_host.trim())) { setErr("شناسه VM فرستایل را بده (خط کامل npx freestyle vm ssh ... را می‌توانی پیست کنی)"); return; }
      } else if (!f.secret) { setErr(f.auth_type === "password" ? "رمز عبور SSH الزامی است" : "متن کلید خصوصی الزامی است"); return; }
      setBusy(true); setErr("");
      try {
        const r = await adminApi.provisionServer({
          name: f.name.trim(), ssh_host: f.ssh_host.trim(), ssh_user: f.ssh_user.trim() || "root",
          auth_type: f.auth_type,
          ...(f.auth_type === "password" ? { ssh_password: f.secret } : f.auth_type === "key" ? { ssh_key: f.secret } : {}),
          tailscale_ip: f.tailscale_ip.trim() || null, region: f.region.trim() || null,
          min_workers: f.min_workers, max_workers: f.max_workers,
        });
        setF({ ...f, secret: "" }); // never keep plaintext in state longer than needed
        await poll(r.id);
      } catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };

    const retry = async () => {
      if (!prov) return;
      setBusy(true); setErr("");
      try { await adminApi.retryProvision(prov.serverId); await poll(prov.serverId); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };

    const STEP_LABELS: Record<string, string> = { connect: "اتصال SSH", docker: "داکر", net: "شبکه خصوصی", engine: "انجین", workers: "ورکرها", agent: "ایجنت", bench: "تست توان", done: "اتمام" };
    const failed = prov?.status === "failed";
    const ready = prov?.status === "ready";

    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          {!prov ? (
            <>
              <h3 className="font-black">افزودن سرور جدید — نصب خودکار</h3>
              <p className="mt-1 text-xs text-muted">IP و مشخصات SSH را بدهید؛ پنل خودش داکر، شبکه خصوصی، انجین، ورکرها و ایجنت را نصب می‌کند.</p>
              {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
              <div className="mt-4 space-y-3">
                <div className="flex gap-2">
                  <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="نام سرور (srv-eu-1) *" className="flex-1 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
                  <input value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })} placeholder="region" className="w-28 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
                </div>
                <div className="flex gap-2">
                  <input value={f.ssh_host} onChange={(e) => setF({ ...f, ssh_host: e.target.value })} placeholder="IP خصوصی / هاست SSH *" dir="ltr" className="flex-1 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 text-left outline-none" />
                  <input value={f.ssh_user} onChange={(e) => setF({ ...f, ssh_user: e.target.value })} placeholder="کاربر" dir="ltr" className="w-28 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 text-left outline-none" />
                </div>
                <div className="flex gap-2 text-xs">
                  <label className={`flex-1 cursor-pointer rounded-xl border px-3 py-2.5 text-center ${f.auth_type === "password" ? "border-brand/60 bg-brand/10 font-bold" : "border-white/10 bg-white/[.04]"}`}><input type="radio" className="hidden" checked={f.auth_type === "password"} onChange={() => setF({ ...f, auth_type: "password" })} /> رمز عبور</label>
                  <label className={`flex-1 cursor-pointer rounded-xl border px-3 py-2.5 text-center ${f.auth_type === "key" ? "border-brand/60 bg-brand/10 font-bold" : "border-white/10 bg-white/[.04]"}`}><input type="radio" className="hidden" checked={f.auth_type === "key"} onChange={() => setF({ ...f, auth_type: "key" })} /> کلید خصوصی</label>
                  <label className={`flex-1 cursor-pointer rounded-xl border px-3 py-2.5 text-center ${f.auth_type === "freestyle" ? "border-brand/60 bg-brand/10 font-bold" : "border-white/10 bg-white/[.04]"}`}><input type="radio" className="hidden" checked={f.auth_type === "freestyle"} onChange={() => setF({ ...f, auth_type: "freestyle" })} /> Freestyle VM</label>
                </div>
                {f.auth_type === "freestyle" ? (
                  <>
                    <textarea value={f.ssh_host} onChange={(e) => setF({ ...f, ssh_host: e.target.value })} placeholder="npx freestyle@latest vm ssh vm-xxxxxxxx --team acct-xxxx  (کل خط را پیست کن)" dir="ltr" rows={2} className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 text-left font-mono text-[11px] outline-none" />
                    <p className="text-[11px] leading-5 text-muted">پنل کل خط را می‌خواند و شناسه VM را خودش برمی‌دارد. اجرای دستورها از طریق CLI فرستایل (FREESTYLE_API_KEY سرور) انجام می‌شود — نیازی به رمز/کلید SSH نیست. عنوان «هاست» فقط شناسه VM است.</p>
                  </>
                ) : f.auth_type === "password"
                  ? <input type="password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} placeholder="رمز عبور SSH *" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
                  : <textarea value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} placeholder="-----BEGIN OPENSSH PRIVATE KEY----- ..." dir="ltr" rows={3} className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 text-left font-mono text-[11px] outline-none" />}
                <input value={f.tailscale_ip} onChange={(e) => setF({ ...f, tailscale_ip: e.target.value })} placeholder="IP تیلسکیل مرکز (اختیاری)" dir="ltr" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 text-left outline-none" />
                <div className="flex gap-2 text-xs">
                  <label className="flex-1">کمینه ورکر<input inputMode="numeric" value={f.min_workers} onChange={(e) => setF({ ...f, min_workers: Math.max(0, parseNum(e.target.value)) })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                  <label className="flex-1">سقف ورکر<input inputMode="numeric" value={f.max_workers} onChange={(e) => setF({ ...f, max_workers: Math.max(1, parseNum(e.target.value)) })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                </div>
                <p className="text-[11px] leading-5 text-muted">رمز/کلید فقط رمزنگاری‌شده نگه داشته می‌شود و هیچ‌جا به متن ساده نمایش داده نمی‌شود.</p>
              </div>
              <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : "نصب خودکار"}</Button></div>
            </>
          ) : (
            <>
              <h3 className="font-black">نصب خودکار {failed ? "ناموفق شد" : ready ? "تمام شد ✅" : "در حال اجراست…"}</h3>
              <div className="mt-3 space-y-2">
                {(["connect", "docker", "net", "engine", "workers", "agent", "bench"] as const).map((s) => {
                  const rec = prov.steps.find((x) => x.step === s);
                  const active = !rec && prov.current_step === s;
                  return (
                    <div key={s} className="flex items-center gap-2 text-xs">
                      <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-black ${rec?.ok ? "bg-emerald-500/20 text-emerald-200" : rec && !rec.ok ? "bg-red-500/20 text-red-200" : active ? "bg-sky-500/20 text-sky-200" : "bg-white/10 text-muted"}`}>
                        {rec?.ok ? "✓" : rec && !rec.ok ? "✕" : active ? "…" : "·"}
                      </span>
                      <span className={active ? "font-bold" : ""}>{STEP_LABELS[s]}</span>
                      {rec && <span className="mr-auto text-[11px] text-muted">{rec.msg_fa}</span>}
                    </div>
                  );
                })}
              </div>
              {failed && (
                <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs leading-6 text-red-200">
                  {prov.steps.filter((x) => !x.ok).map((x) => x.msg_fa).join(" ")}
                  <div className="mt-2"><Button size="sm" onClick={retry} disabled={busy}>تلاش مجدد از همان مرحله</Button></div>
                </div>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={onClose}>بستن</Button>
                {ready && <Button onClick={onDone}>متوجه شدم</Button>}
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Server detail modal (US-03): caps + autoscale + capability + provision log
  // ---------------------------------------------------------------------------
  function ServerDetailModal({ serverId, onClose, onToast }: { serverId: number; onClose: () => void; onToast: (m: string) => void }) {
    const [d, setD] = useState<null | Awaited<ReturnType<typeof adminApi.getServer>>>(null);
    const [err, setErr] = useState("");
    const [busy, setBusy] = useState(false);
    const [caps, setCaps] = useState({ min_workers: 1, max_workers: 4, autoscale_enabled: true });
    useEffect(() => {
      adminApi.getServer(serverId)
        .then((r) => { setD(r); setCaps({ min_workers: r.min_workers, max_workers: r.max_workers, autoscale_enabled: r.autoscale_enabled }); })
        .catch((e: Error) => setErr(e.message));
    }, [serverId]);
    const save = async () => {
      if (caps.max_workers < caps.min_workers) { setErr("سقف نمی‌تواند از کمینه کمتر باشد"); return; }
      setBusy(true); setErr("");
      try { await adminApi.manageServer(serverId, caps); onToast("ذخیره شد"); const r = await adminApi.getServer(serverId); setD(r); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          {!d ? (<p className="text-xs text-muted">{err || "در حال بارگذاری…"}</p>) : (
            <>
              <div className="flex items-center justify-between">
                <h3 className="font-black">سرور «{d.name}»</h3>
                <span className="text-xs text-muted">{d.status}</span>
              </div>
              {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <div className="rounded-xl border border-white/10 p-3">
                  <h4 className="mb-2 text-xs font-black text-muted">سقف و مقیاس خودکار</h4>
                  <div className="flex gap-2 text-xs">
                    <label className="flex-1">کمینه<input inputMode="numeric" value={caps.min_workers} onChange={(e) => setCaps({ ...caps, min_workers: Math.max(0, parseNum(e.target.value)) })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                    <label className="flex-1">سقف<input inputMode="numeric" value={caps.max_workers} onChange={(e) => setCaps({ ...caps, max_workers: Math.max(1, parseNum(e.target.value)) })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                  </div>
                  <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={caps.autoscale_enabled} onChange={(e) => setCaps({ ...caps, autoscale_enabled: e.target.checked })} /> مقیاس خودکار بر اساس صف</label>
                  <div className="mt-2"><Button size="sm" onClick={save} disabled={busy}>ذخیره</Button></div>
                </div>
                <div className="rounded-xl border border-white/10 p-3 text-xs leading-6">
                  <h4 className="mb-2 text-xs font-black text-muted">توان و سلامت</h4>
                  <div>انجین محلی: <span className={d.engine_healthy ? "text-emerald-300" : "text-red-300"}>{d.engine_healthy ? "سالم" : "خراب"}</span></div>
                  <div>داکر: {d.docker_ok ? "نصب است" : "نامشخص"}</div>
                  {d.capability
                    ? <div className="mt-1 text-muted">تست توان: <code dir="ltr" className="rounded bg-black/40 px-1">{JSON.stringify(d.capability)}</code>{d.capability_warning && <span className="mr-1 text-amber-200">— سرور ضعیف</span>}</div>
                    : <div className="text-muted">تست توان هنوز اجرا نشده</div>}
                  <div className="text-muted">نصب: {d.provision_state ?? "—"}{d.provision_step ? ` (${d.provision_step})` : ""} · SSH: {d.has_ssh ? "ثبت شده (رمزنگاری‌شده)" : "ندارد"}</div>
                </div>
              </div>
              <div className="mt-3 rounded-xl border border-white/10 p-3">
                <h4 className="mb-2 text-xs font-black text-muted">ورکرها ({d.workers.length})</h4>
                {d.workers.length === 0 ? <p className="text-xs text-muted">ورکری ثبت نشده</p> : (
                  <div className="flex flex-wrap gap-1.5 text-[11px]">
                    {d.workers.map((w) => <span key={w.name} className={`rounded-full px-2 py-0.5 ${w.status === "ready" ? "bg-emerald-500/15 text-emerald-200" : "bg-white/10 text-muted"}`}>{w.name}</span>)}
                  </div>
                )}
              </div>
              {d.provision_log.length > 0 && (
                <div className="mt-3 rounded-xl border border-white/10 p-3">
                  <h4 className="mb-2 text-xs font-black text-muted">لاگ نصب</h4>
                  <div className="space-y-1 text-[11px] leading-5">
                    {d.provision_log.map((s, i) => <div key={i} className="flex gap-2"><span>{s.ok ? "✓" : "✕"}</span><span className="font-bold">{s.step}</span><span className="text-muted">{s.msg_fa}</span></div>)}
                  </div>
                </div>
              )}
              <div className="mt-4 flex justify-end"><Button onClick={onClose}>بستن</Button></div>
            </>
          )}
        </div>
      </div>
    );
  }

  function JoinServerModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const [f, setF] = useState({ name: "", region: "", desired_workers: 2, worker_concurrency: 4, cpu_limit: "2.0", mem_limit: "2G" });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const [created, setCreated] = useState<{ id: number; name: string; join_token: string } | null>(null);
    const submit = async () => {
      if (!f.name.trim()) { setErr("نام سرور الزامی است (مثلاً srv-eu-1)"); return; }
      setBusy(true); setErr("");
      try { const c = await adminApi.createServer({ name: f.name.trim(), region: f.region.trim() || null, desired_workers: f.desired_workers, worker_concurrency: f.worker_concurrency, cpu_limit: f.cpu_limit, mem_limit: f.mem_limit }); setCreated(c); }
      catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    const cmd = created ? `curl -fsSL http://206.245.166.14:9001/install/${created.join_token} | bash` : "";
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={created ? onClose : onClose}>
        <div className="w-full max-w-xl rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          {!created ? (
            <>
              <h3 className="font-black">افزودن سرور جدید</h3>
              <p className="mt-1 text-xs text-muted">سرور ثبت می‌شود و یک دستور نصب یک‌خطی می‌گیرید که روی سرور جدید اجرا می‌کنید.</p>
              {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
              <div className="mt-4 space-y-3">
                <div className="flex gap-2">
                  <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="نام سرور (srv-eu-1) *" className="flex-1 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
                  <input value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })} placeholder="region" className="w-28 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
                </div>
                <div className="flex gap-2 text-xs">
                  <label className="flex-1">ورکرها<input inputMode="numeric" value={f.desired_workers} onChange={(e) => setF({ ...f, desired_workers: parseNum(e.target.value) })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                  <label className="flex-1">همزمانی/ورکر<input inputMode="numeric" value={f.worker_concurrency} onChange={(e) => setF({ ...f, worker_concurrency: Math.max(1, parseNum(e.target.value)) })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                </div>
                <div className="flex gap-2 text-xs">
                  <label className="flex-1">CPU<input value={f.cpu_limit} onChange={(e) => setF({ ...f, cpu_limit: e.target.value })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                  <label className="flex-1">RAM<input value={f.mem_limit} onChange={(e) => setF({ ...f, mem_limit: e.target.value })} className="mt-1 w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2" /></label>
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : "ثبت و گرفتن دستور"}</Button></div>
            </>
          ) : (
            <>
              <h3 className="font-black">سرور «{created.name}» ثبت شد ✅</h3>
              <p className="mt-2 text-xs text-muted">این دستور را روی سرور جدید اجرا کنید (docker لازم است — اگر نبود خودش نصب می‌کند):</p>
              <div className="relative mt-3">
                <pre dir="ltr" className="overflow-auto rounded-xl border border-white/10 bg-black/60 p-3 text-left text-[11px] leading-5 text-emerald-200">{cmd}</pre>
                <button onClick={() => { navigator.clipboard.writeText(cmd); }} className="absolute left-2 top-2 rounded-lg bg-white/10 px-2 py-1 text-[10px] font-bold hover:bg-white/20">کپی</button>
              </div>
              <p className="mt-3 text-[11px] leading-5 text-muted">ایجنت ظرف ~۱۰ ثانیه به پنل وصل می‌شود و {f.desired_workers} ورکر بالا می‌آورد. تعداد ورکرها بعداً از همین جدول با +/− قابل تغییر است.</p>
              <div className="mt-4 flex justify-end"><Button onClick={onDone}>متوجه شدم</Button></div>
            </>
          )}
        </div>
      </div>
    );
  }

  function JoinShowModal({ server, onClose, onToast }: { server: ServerRow; onClose: () => void; onToast: (m: string) => void }) {
    const cmd = `curl -fsSL http://206.245.166.14:9001/install/${server.join_token} | bash`;
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="w-full max-w-xl rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">دستور نصب «{server.name}»</h3>
          <div className="relative mt-3">
            <pre dir="ltr" className="overflow-auto rounded-xl border border-white/10 bg-black/60 p-3 text-left text-[11px] leading-5 text-emerald-200">{cmd}</pre>
            <button onClick={() => { navigator.clipboard.writeText(cmd); onToast("کپی شد"); }} className="absolute left-2 top-2 rounded-lg bg-white/10 px-2 py-1 text-[10px] font-bold hover:bg-white/20">کپی</button>
          </div>
          <div className="mt-4 flex justify-end"><Button onClick={onClose}>بستن</Button></div>
        </div>
      </div>
    );
  }

  function FleetModal({ node, onClose, onDone }: { node?: EngineRow; onClose: () => void; onDone: () => void }) {
    const [f, setF] = useState({ name: node?.name ?? "", url: node?.url ?? "", api_key: "", region: node?.region ?? "", max_concurrency: node?.max_concurrency ?? 10, is_enabled: node?.is_enabled ?? true });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState("");
    const submit = async () => {
      if (!f.name.trim() || !f.url.trim()) { setErr("نام و URL الزامی است"); return; }
      setBusy(true); setErr("");
      try {
        const payload = { name: f.name.trim(), url: f.url.trim(), api_key: f.api_key.trim() || null, region: f.region.trim() || null, max_concurrency: f.max_concurrency, is_enabled: f.is_enabled };
        if (node) await adminApi.updateFleet(node.id, payload);
        else await adminApi.addFleet(payload);
        onDone();
      } catch (e) { setErr((e as Error).message); }
      finally { setBusy(false); }
    };
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
        <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 p-5 text-sm" onClick={(e) => e.stopPropagation()} dir="rtl">
          <h3 className="font-black">{node ? "ویرایش موتور" : "افزودن موتور"}</h3>
          {err && <p className="mt-2 rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-200">{err}</p>}
          <div className="mt-4 space-y-3">
            <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="نام (engine-eu-1)" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="URL (http://HOST:8899)" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 font-mono text-xs outline-none" dir="ltr" />
            <input value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} placeholder="API Key (اختیاری — خالی = سراسری)" type="password" className="w-full rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
            <div className="flex gap-2">
              <input value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })} placeholder="region (اختیاری)" className="flex-1 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5 outline-none" />
              <input inputMode="numeric" value={f.max_concurrency} onChange={(e) => setF({ ...f, max_concurrency: parseNum(e.target.value) })} className="w-28 rounded-xl border border-white/10 bg-white/[.04] px-3 py-2.5" />
            </div>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.is_enabled} onChange={(e) => setF({ ...f, is_enabled: e.target.checked })} /> فعال</label>
          </div>
          <div className="mt-5 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>انصراف</Button><Button onClick={submit} disabled={busy}>{busy ? "…" : node ? "ذخیره" : "افزودن"}</Button></div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Live
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // US10 dashboard widgets wrapper (recharts) — defined in components/fleet
  // ---------------------------------------------------------------------------
  function FleetWidgets({ fleet, loadPts, data }: {
    fleet: import("@/api/admin").FleetLive;
    loadPts: Array<{ t: string; v: number }>;
    data: import("@/api/admin").MonitorFull;
  }) {
    return (
      <>
        <Card className="p-4">
          <FleetCards fleet={fleet.fleet} />
        </Card>
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="p-4">
            <div className="mb-2 text-xs font-extrabold">بار زنده ناوگان (هر ۵ ثانیه)</div>
            <LoadChart points={loadPts.length ? loadPts : [{ t: fleet.ts, v: fleet.fleet.load }]} />
          </Card>
          <Card className="p-4">
            <div className="mb-2 text-xs font-extrabold">توزیع بار بین گره‌ها</div>
            <DistDonut slices={fleet.servers.map((s) => ({ name: s.name, value: s.load }))} />
          </Card>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="p-4">
            <div className="mb-2 text-xs font-extrabold">تسک‌ها (۱ ساعت اخیر)</div>
            <TasksBar ok={data.tasks_1h - data.failed_1h} fail={data.failed_1h} pending={fleet.fleet.queue} />
          </Card>
          <Card className="p-4">
            <div className="mb-2 text-xs font-extrabold">نقشه سلامت گره‌ها</div>
            <HealthMap servers={fleet.servers} />
          </Card>
        </div>
        <Card className="p-4">
          <div className="mb-2 text-xs font-extrabold">تسک‌های زنده ({fleet.tasks_live.length})</div>
          <LiveTasks tasks={fleet.tasks_live} />
        </Card>
      </>
    );
  }

  function LiveTab() {
    const [data, setData] = useState<import("@/api/admin").MonitorFull | null>(null);
    const [fleet, setFleet] = useState<import("@/api/admin").FleetLive | null>(null);
    const [loadPts, setLoadPts] = useState<Array<{ t: string; v: number }>>([]);
    const [err, setErr] = useState("");
    const timer = useRef<number | null>(null);
    const load = useCallback(() => {
      adminApi.getMonitorFull().then(setData).catch((e: Error) => setErr(e.message));
      adminApi.getFleetLive().then((f) => {
        setFleet(f);
        setLoadPts((prev) => [...prev.slice(-119), { t: f.ts, v: f.fleet.load }]);
      }).catch(() => {});
    }, []);
    useEffect(() => { load(); timer.current = window.setInterval(load, 5000); return () => { if (timer.current) clearInterval(timer.current); }; }, [load]);

    if (err) return <Card className="p-6 text-sm text-red-300">{err}</Card>;
    if (!data) return <Card className="p-6 text-sm text-muted">در حال بارگذاری…</Card>;

    const failRate = data.tasks_1h ? Math.round(data.failed_1h * 100 / data.tasks_1h) : 0;
    return (
      <div className="space-y-4">
        {/* US10: fleet charts (recharts) — live load + distribution + health + live tasks */}
        {fleet && <FleetWidgets fleet={fleet} loadPts={loadPts} data={data} />}
        {/* KPI row */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          <Card className="p-4"><div className="text-[11px] font-bold text-muted">تسک یک ساعت اخیر</div><div className="mt-1 text-xl font-black">{data.tasks_1h}</div></Card>
          <Card className="p-4"><div className="text-[11px] font-bold text-muted">ناموفق (۱س)</div><div className={`mt-1 text-xl font-black ${data.failed_1h ? "text-red-200" : "text-emerald-200"}`}>{data.failed_1h} <span className="text-xs font-bold">({failRate}%)</span></div></Card>
          <Card className="p-4"><div className="text-[11px] font-bold text-muted">عمق صف</div><div className="mt-1 text-xl font-black text-amber-200">{data.queue_depth ?? "—"}</div></Card>
          <Card className="p-4"><div className="text-[11px] font-bold text-muted">سرورها</div><div className="mt-1 text-xl font-black">{data.per_server.length}</div></Card>
          <Card className="p-4"><div className="text-[11px] font-bold text-muted">ورکر آنلاین</div><div className="mt-1 text-xl font-black text-emerald-200">{data.per_server.reduce((a, s) => a + s.online_workers, 0)}</div></Card>
          <Card className="p-4"><div className="text-[11px] font-bold text-muted">ورکر ثبت‌شده</div><div className="mt-1 text-xl font-black">{data.per_server.reduce((a, s) => a + s.registered_workers, 0)}</div></Card>
        </div>

        {/* dispatcher live routing */}
        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-extrabold">دیسپچر — مسیریابی زنده (صف اختصاصی هر ورکر)</div>
            <div className="flex flex-wrap gap-2 text-[10px]">
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-muted">مسیریابی‌شده: <b className="text-sky-300">{data.dispatcher?.stats?.routed ?? 0}</b></span>
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-muted">نجات از ورکر مرده: <b className="text-emerald-300">{data.dispatcher?.stats?.rescued_from_dead ?? 0}</b></span>
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-muted">بازگشتی از fallback: <b className="text-amber-300">{data.dispatcher?.stats?.reaped ?? 0}</b></span>
              <span className="rounded-full bg-white/5 px-2 py-0.5 text-muted">عمق fallback: <b className={data.dispatcher?.fallback_depth ? "text-red-300" : "text-emerald-300"}>{data.dispatcher?.fallback_depth ?? 0}</b></span>
            </div>
          </div>
          <div className="overflow-auto">
            <table className="w-full min-w-[560px] text-xs">
              <thead className="bg-white/[.04] text-[11px] text-muted"><tr>
                <th className="px-3 py-2 text-right">ورکر</th>
                <th className="px-3 py-2 text-right">وضعیت</th>
                <th className="px-3 py-2 text-right">ظرفیت همزمان</th>
                <th className="px-3 py-2 text-right">صف اختصاصی</th>
                <th className="px-3 py-2 text-right">در حال اجرا</th>
                <th className="px-3 py-2 text-right">بار کل</th>
              </tr></thead>
              <tbody>
                {(data.dispatcher?.workers ?? []).length === 0 && <tr><td colSpan={6} className="px-3 py-4 text-center text-muted">ورکری در رجیستری نیست</td></tr>}
                {(data.dispatcher?.workers ?? []).map((w) => {
                  const cap = data.dispatcher!.workers.reduce((a, x) => Math.max(a, x.load), 1);
                  const hot = w.load >= w.concurrency * 2;
                  return (
                    <tr key={w.name} className="border-t border-white/5">
                      <td className="px-3 py-2 font-bold">{w.name}</td>
                      <td className="px-3 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${w.status === "ready" ? "bg-emerald-500/15 text-emerald-200" : "bg-red-500/15 text-red-200"}`}>{w.status === "ready" ? "آماده" : w.status}</span>
                      </td>
                      <td className="px-3 py-2 text-muted">{w.concurrency}</td>
                      <td className="px-3 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${w.queue_depth ? "bg-amber-500/15 text-amber-200" : "bg-white/5 text-muted"}`}>{w.queue_depth}</span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${w.inflight ? "bg-sky-500/15 text-sky-200" : "bg-white/5 text-muted"}`}>{w.inflight}</span>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-24 overflow-hidden rounded-full bg-white/10">
                            <div className={`h-full rounded-full ${hot ? "bg-gradient-to-l from-red-400 to-amber-400" : "bg-gradient-to-l from-sky-400 to-emerald-400"}`} style={{ width: `${Math.min((w.load / cap) * 100, 100)}%` }} />
                          </div>
                          <span className={`font-black ${hot ? "text-red-300" : ""}`}>{w.load}</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* throughput chart */}
        <Card className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-xs font-extrabold">روند تسک‌ها (۱ ساعت اخیر · باکت ۵ دقیقه)</div>
            <div className="flex gap-3 text-[10px] text-muted">
              <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-emerald-400" /> تکمیل</span>
              <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-red-400" /> خطا</span>
              <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-sm bg-sky-400" /> کل</span>
            </div>
          </div>
          <ThroughputChart series={data.series_15min} />
        </Card>

        {/* per-server cards */}
        <div className="grid gap-4 md:grid-cols-2">
          {data.per_server.map((s) => (
            <Card key={s.id} className="p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={`inline-block h-2 w-2 rounded-full ${s.status === "online" ? "bg-emerald-400 animate-pulse" : s.status === "pending" ? "bg-amber-400" : "bg-red-400"}`} />
                  <span className="text-sm font-black">{s.name}</span>
                  {s.region && <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted">{s.region}</span>}
                </div>
                <span className={`text-[11px] font-bold ${s.online_workers === s.desired_workers ? "text-emerald-300" : "text-amber-300"}`}>{s.online_workers}/{s.desired_workers} ورکر</span>
              </div>
              <div className="mt-3 grid grid-cols-4 gap-2 text-center text-[11px]">
                <div className="rounded-lg bg-white/[.03] py-1.5"><div className="text-muted">تسک ۱س</div><div className="mt-0.5 font-black">{s.tasks_1h}</div></div>
                <div className="rounded-lg bg-white/[.03] py-1.5"><div className="text-muted">خطا ۱س</div><div className={`mt-0.5 font-black ${s.failed_1h ? "text-red-300" : ""}`}>{s.failed_1h}</div></div>
                <div className="rounded-lg bg-white/[.03] py-1.5"><div className="text-muted">CPU</div><div className="mt-0.5 font-black">{s.cpu_limit ?? "—"}</div></div>
                <div className="rounded-lg bg-white/[.03] py-1.5"><div className="text-muted">RAM</div><div className="mt-0.5 font-black">{s.mem_limit ?? "—"}</div></div>
              </div>
              {s.host && s.host.cpu_count ? (
                <div className="mt-3 space-y-1.5">
                  <HostBar label="CPU" value={s.host.cpu_count} unit="vCPU" />
                  <HostBar label="RAM" value={s.host.mem_total_gb ?? 0} unit="GB" />
                  <HostBar label="دیسک" value={s.host.disk_used_pct ?? 0} unit="%" pct />
                </div>
              ) : null}
            </Card>
          ))}
        </div>

        {/* per-worker heat table */}
        <Card className="p-4">
          <div className="mb-3 text-xs font-extrabold">ورکرها — چه کسی چقدر کار کرده (۱ ساعت اخیر)</div>
          <div className="overflow-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead className="bg-white/[.04] text-[11px] text-muted"><tr>
                <th className="px-3 py-2 text-right">ورکر</th><th className="px-3 py-2 text-right">سرور</th>
                <th className="px-3 py-2 text-right">تسک</th><th className="px-3 py-2 text-right">تکمیل</th>
                <th className="px-3 py-2 text-right">خطا</th><th className="px-3 py-2 text-right">موفقیت</th>
                <th className="px-3 py-2 text-right">میانگین زمان</th><th className="px-3 py-2 text-right">بار</th>
              </tr></thead>
              <tbody>
                {data.per_worker.length === 0 && <tr><td colSpan={8} className="px-3 py-4 text-center text-muted">تسکی در ساعت گذشته نبوده</td></tr>}
                {data.per_worker.map((w) => {
                  const max = Math.max(...data.per_worker.map((x) => x.total), 1);
                  return (
                    <tr key={w.name} className="border-t border-white/5">
                      <td className="px-3 py-2 font-bold">{w.name}</td>
                      <td className="px-3 py-2 text-muted">{w.server}</td>
                      <td className="px-3 py-2 font-black">{w.total}</td>
                      <td className="px-3 py-2 text-emerald-300">{w.completed}</td>
                      <td className={`px-3 py-2 ${w.failed ? "text-red-300" : "text-muted"}`}>{w.failed}</td>
                      <td className="px-3 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${w.success_pct >= 90 ? "bg-emerald-500/15 text-emerald-200" : w.success_pct >= 50 ? "bg-amber-500/15 text-amber-200" : "bg-red-500/15 text-red-200"}`}>{w.success_pct}%</span>
                      </td>
                      <td className="px-3 py-2 text-muted">{w.avg_sec != null ? `${w.avg_sec}s` : "—"}</td>
                      <td className="px-3 py-2">
                        <div className="h-2 w-24 overflow-hidden rounded-full bg-white/10">
                          <div className="h-full rounded-full bg-gradient-to-l from-sky-400 to-emerald-400" style={{ width: `${(w.total / max) * 100}%` }} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* live feed */}
        <Card className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-xs font-extrabold">جریان زنده تسک‌ها (۱۵ اخیر)</div>
            <span className="flex items-center gap-1 text-[10px] text-emerald-300"><i className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" /> زنده</span>
          </div>
          <div className="space-y-1.5">
            {data.recent.map((t) => (
              <div key={t.task_id} className="flex flex-wrap items-center gap-2 rounded-lg border border-white/5 bg-white/[.02] px-3 py-2 text-[11px]">
                <span className={`rounded-full px-2 py-0.5 font-bold ${t.status === "completed" ? "bg-emerald-500/15 text-emerald-200" : t.status === "running" ? "bg-sky-500/15 text-sky-200" : t.status === "failed" ? "bg-red-500/15 text-red-200" : "bg-amber-500/15 text-amber-200"}`}>{t.status === "completed" ? "تکمیل" : t.status === "running" ? "در اجرا" : t.status === "failed" ? "خطا" : "صف"}</span>
                <span className="font-bold">{t.type}</span>
                <span className="text-muted">→</span>
                <span className="text-sky-300">{t.worker ?? "—"}</span>
                <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted">{t.server}</span>
                {t.duration_sec != null && <span className="text-muted">{t.duration_sec}s</span>}
                {t.error && <span className="truncate text-red-300/80" title={t.error}>⚠ {t.error.slice(0, 60)}</span>}
                <span className="ml-auto text-muted">{fmtFa(t.created_at)}</span>
              </div>
            ))}
            {data.recent.length === 0 && <p className="text-xs text-muted">تسکی ثبت نشده</p>}
          </div>
        </Card>

        <p className="text-center text-[11px] text-muted">بروزرسانی خودکار هر ۵ ثانیه · {fmtFa(data.ts)}</p>
      </div>
    );
  }

  function HostBar({ label, value, unit, pct }: { label: string; value: number; unit: string; pct?: boolean }) {
    const maxVal = pct ? 100 : Math.max(value, 1);
    const w = Math.min((value / maxVal) * 100, 100);
    return (
      <div className="flex items-center gap-2 text-[10px]">
        <span className="w-8 text-muted">{label}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
          <div className={`h-full rounded-full ${pct && value > 85 ? "bg-red-400" : pct && value > 60 ? "bg-amber-400" : "bg-emerald-400"}`} style={{ width: `${w}%` }} />
        </div>
        <span className="w-14 text-left text-muted" dir="ltr">{pct ? `${Math.round(value)}%` : `${value}${unit}`}</span>
      </div>
    );
  }

  function ThroughputChart({ series }: { series: { t: number; total: number; completed: number; failed: number }[] }) {
    // pure-SVG stacked bars — no chart lib
    const W = 720, H = 160, PAD = 24;
    if (!series.length) return <p className="py-8 text-center text-xs text-muted">داده‌ای در ساعت گذشته نیست</p>;
    const max = Math.max(...series.map((p) => p.total), 1);
    const bw = (W - PAD * 2) / series.length;
    return (
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full" style={{ direction: "ltr" }}>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={PAD} x2={W - PAD} y1={H - f * (H - PAD * 2) - PAD} y2={H - f * (H - PAD * 2) - PAD} stroke="rgba(255,255,255,.07)" />
            <text x={4} y={H - f * (H - PAD * 2) - PAD + 3} fill="rgba(255,255,255,.35)" fontSize="9">{Math.round(max * f)}</text>
          </g>
        ))}
        {series.map((p, i) => {
          const x = PAD + i * bw + bw * 0.15;
          const w = bw * 0.7;
          const hTotal = (p.total / max) * (H - PAD * 2);
          const hFail = (p.failed / max) * (H - PAD * 2);
          const hDone = (p.completed / max) * (H - PAD * 2);
          return (
            <g key={p.t}>
              <rect x={x} y={H - PAD - hTotal} width={w} height={hTotal} rx="2" fill="rgba(56,189,248,.35)" />
              <rect x={x} y={H - PAD - hDone} width={w} height={hDone} rx="2" fill="rgb(52,211,153)" />
              <rect x={x} y={H - PAD - hFail} width={w} height={hFail} rx="2" fill="rgb(248,113,113)" />
              <title>{`ساعت ${new Date(p.t * 1000).toLocaleTimeString("fa-IR")} — کل: ${p.total} · تکمیل: ${p.completed} · خطا: ${p.failed}`}</title>
            </g>
          );
        })}
      </svg>
    );
  }

  // ---------------------------------------------------------------------------
  // Logs
  // ---------------------------------------------------------------------------

  function LogsTab() {
    const [rows, setRows] = useState<Array<{ id: number; user_id: number; ip: string | null; user_agent: string; created_at: string | null }> | null>(null);
    const [err, setErr] = useState("");
    const [filter, setFilter] = useState("");
    const load = useCallback(() => {
      const uid = filter.trim() ? Number(filter.trim()) : undefined;
      adminApi.getLoginLogs(Number.isFinite(uid as number) ? uid : undefined).then(setRows).catch((e: Error) => setErr(e.message));
    }, [filter]);
    useEffect(() => { load(); }, [load]);

    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="فیلتر user_id (خالی = همه)" className="w-44 rounded-xl border border-white/10 bg-white/[.03] px-3 py-2 text-sm outline-none" />
          <Button variant="outline" onClick={load} className="gap-1.5"><RefreshCw size={14} /> بروزرسانی</Button>
        </div>
        {err && <Card className="p-3 text-sm text-red-300">{err}</Card>}
        <Card className="overflow-hidden">
          <div className="overflow-auto">
            <table className="w-full min-w-[640px] text-xs">
              <thead className="bg-white/[.04] text-[11px] text-muted"><tr><th className="px-3 py-2 text-right">#</th><th className="px-3 py-2 text-right">user_id</th><th className="px-3 py-2 text-right">IP</th><th className="px-3 py-2 text-right">User-Agent</th><th className="px-3 py-2 text-right">زمان</th></tr></thead>
              <tbody>
                {!rows ? <tr><td colSpan={5} className="px-3 py-8 text-center text-muted">در حال بارگذاری…</td></tr>
                  : rows.length === 0 ? <tr><td colSpan={5} className="px-3 py-8 text-center text-muted">لاگی نیست</td></tr>
                    : rows.map((r) => (
                      <tr key={r.id} className="border-t border-white/5"><td className="px-3 py-2 text-muted">{r.id}</td><td className="px-3 py-2 font-bold">{r.user_id}</td><td className="px-3 py-2 font-mono text-[11px]">{r.ip ?? "—"}</td><td className="px-3 py-2 max-w-[360px] truncate text-muted">{r.user_agent || "—"}</td><td className="px-3 py-2 text-muted">{fmtFa(r.created_at)}</td></tr>
                    ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    );
  }
