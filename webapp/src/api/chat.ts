import { api } from "@/api/client";

export type ChatMessage = {
  message_id?: string;
  role: string;
  content: string;
  created_at?: string;
};

export type SessionRow = {
  session_id?: string;
  vibe_session_id?: string;
  id?: string;
  title?: string;
  name?: string;
  created_at?: string;
};

export const sid = (s: SessionRow) => s.vibe_session_id || s.session_id || s.id || "";

export const listSessions = () => api<SessionRow[]>("/api/v1/vibe/sessions");

export const createSession = () =>
  api<{ session_id?: string }>("/api/v1/vibe/sessions", { method: "POST" });

export const getMessages = (id: string) =>
  api<ChatMessage[]>(`/api/v1/vibe/sessions/${id}/messages`);

export const sendMessage = (id: string, content: string) =>
  api<unknown>(`/api/v1/vibe/sessions/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const cancelRun = (id: string) =>
  api<{ status?: string }>(`/api/v1/vibe/sessions/${id}/cancel`, { method: "POST" });

/** Wait until a NEW assistant message (index >= preCount) arrives. */
export async function waitForNewAnswer(
  id: string,
  preCount: number,
  opts: { maxWait?: number; onTick?: (seconds: number) => void; signal?: { cancelled: boolean } } = {}
): Promise<string | null> {
  const { maxWait = 240, onTick, signal } = opts;
  for (let i = 0; i < maxWait; i++) {
    if (signal?.cancelled) return null;
    await new Promise((r) => setTimeout(r, 1000));
    onTick?.(i + 1);
    try {
      const msgs = await getMessages(id);
      if (!Array.isArray(msgs)) continue;
      const fresh = msgs.slice(preCount);
      for (let j = fresh.length - 1; j >= 0; j--) {
        const m = fresh[j];
        if ((m.role === "assistant" || m.role === "ai") && m.content) return m.content;
      }
    } catch {
      /* transient network error — keep polling */
    }
  }
  return null;
}
