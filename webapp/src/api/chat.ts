import { api, auth } from "@/api/client";

export type SessionRow = {
  session_id?: string;
  id?: string;
  vibe_session_id?: string;
  title?: string;
  name?: string;
  updated_at?: string;
  created_at?: string;
};

export type ChatMessage = {
  role: "user" | "assistant" | "ai" | string;
  content: string;
  created_at?: string;
};

export const sid = (s: SessionRow) => s.vibe_session_id || s.session_id || s.id || "";

export const listSessions = () => api<SessionRow[]>("/api/v1/vibe/sessions");

export const createSession = (title: string) =>
  api<{ session_id?: string }>("/api/v1/vibe/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });

export const getMessages = (id: string) =>
  api<ChatMessage[]>(`/api/v1/vibe/sessions/${id}/messages`);

export const sendMessage = (id: string, content: string) =>
  api<unknown>(`/api/v1/vibe/sessions/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const cancelRun = (id: string) =>
  api<{ status?: string }>(`/api/v1/vibe/sessions/${id}/cancel`, { method: "POST" });

export const renameSession = (id: string, title: string) =>
  api<{ status: string; title: string }>(`/api/v1/vibe/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });

/**
 * Stream the in-flight answer via the gateway SSE proxy (/sessions/{id}/events),
 * calling onDelta for every text chunk — the chat bubble types itself live instead
 * of appearing only after the whole LLM completion (8–16s of silence today).
 * Resolves with the final text when attempt.completed fires; falls back to
 * waitForNewAnswer-style polling if SSE is unavailable or the timeout hits.
 */
export function streamAnswer(
  id: string,
  opts: {
    maxWait?: number;
    onDelta?: (fullText: string) => void;
    signal?: { cancelled: boolean };
  } = {}
): { done: Promise<string | null>; cancel: () => void } {
  const { maxWait = 240, onDelta, signal } = opts;
  const controller = new AbortController();
  let finalText: string | null = null;

  const done = (async () => {
    const started = Date.now();
    try {
      const base = (typeof window !== "undefined" ? window.location.origin : "");
      const resp = await fetch(`${base}/api/v1/vibe/sessions/${id}/events?replay=active`, {
        headers: { Authorization: `Bearer ${auth.token}` },
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`SSE ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let parts: string[] = [];
      while (true) {
        if (signal?.cancelled) { controller.abort(); return null; }
        if (Date.now() - started > maxWait * 1000) { controller.abort(); break; }
        const { value, done: rdDone } = await reader.read();
        if (rdDone) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let etype = "", data = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) etype = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (!data) continue;
          let ev: Record<string, unknown> = {};
          try { ev = JSON.parse(data); } catch { continue; }
          if (etype === "text_delta" && typeof ev.delta === "string") {
            parts.push(ev.delta);
            onDelta?.(parts.join(""));
          } else if (etype === "thinking_done" && typeof ev.content === "string") {
            // final answer arrives here too; prefer it if deltas missed it
            if (!parts.length) { parts = [ev.content]; onDelta?.(ev.content); }
            finalText = parts.join("");
          } else if (etype === "attempt.completed") {
            finalText = finalText || parts.join("") || (typeof ev.summary === "string" ? ev.summary : null);
            controller.abort();
            return finalText;
          }
        }
      }
    } catch {
      /* SSE unavailable (auth/proxy) — caller falls back to polling */
      return finalText;
    }
    return finalText;
  })();

  return { done, cancel: () => controller.abort() };
}

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
