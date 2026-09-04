import { useEffect, useState } from "react";
import { auth } from "@/api/client";

export function useAuth() {
  const [authed, setAuthed] = useState(() => Boolean(auth.token));
  return { authed, username: auth.username };
}

export function useToast() {
  const [toast, setToast] = useState<{ msg: string; tone: "ok" | "err" } | null>(null);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(t);
  }, [toast]);
  return {
    toast,
    show: (msg: string, tone: "ok" | "err" = "ok") => setToast({ msg, tone }),
  };
}
