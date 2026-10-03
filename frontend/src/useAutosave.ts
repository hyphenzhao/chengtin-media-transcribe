import { useEffect, useRef, useState } from "react";
import { api, type Rec } from "./api";

export function useAutosave(
  key: string,
  path: string,
  initial: string,
  version: number,
  refresh: () => void,
  error: (s: string) => void,
) {
  const cacheKey = "chengtin-draft-" + key;
  const recovered = useRef<{ text: string; version: number } | null>(
    (() => {
      try {
        return JSON.parse(localStorage.getItem(cacheKey) || "null");
      } catch {
        return null;
      }
    })(),
  );
  const state = useRef({
    text: recovered.current?.text ?? initial,
    version: recovered.current?.version ?? version,
    dirty: !!recovered.current,
    blocked: false,
  });
  const [text, setText] = useState(state.current.text),
    [status, setStatus] = useState(recovered.current ? "已恢复草稿" : "已保存"),
    [conflict, setConflict] = useState(false),
    [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const request = useRef<Promise<boolean> | null>(null);
  function cache() {
    localStorage.setItem(
      cacheKey,
      JSON.stringify({
        text: state.current.text,
        version: state.current.version,
      }),
    );
  }
  async function flush(): Promise<boolean> {
    if (timer.current) clearTimeout(timer.current);
    if (state.current.blocked) return false;
    if (request.current) {
      await request.current;
      return state.current.dirty ? flush() : !state.current.blocked;
    }
    if (!state.current.dirty) return true;
    const sent = state.current.text,
      base = state.current.version;
    setSaving(true);
    setStatus("保存中");
    request.current = (async () => {
      try {
        const result = await api<Rec>(path, "PATCH", {
          version: base,
          data: { text: sent },
        });
        state.current.version = result.version;
        state.current.dirty = state.current.text !== sent;
        if (state.current.dirty) cache();
        else localStorage.removeItem(cacheKey);
        setStatus("已自动保存");
        refresh();
        return true;
      } catch (e) {
        state.current.blocked = true;
        cache();
        setConflict(true);
        setStatus("草稿已保留");
        refresh();
        error((e as Error).message);
        return false;
      }
    })();
    const ok = await request.current;
    request.current = null;
    setSaving(false);
    if (ok && state.current.dirty) return flush();
    return ok;
  }
  function change(value: string) {
    state.current.text = value;
    state.current.dirty = true;
    setText(value);
    setStatus("未保存");
    cache();
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), 1000);
  }
  function reset(value: string, nextVersion: number) {
    if (state.current.dirty) return;
    state.current = {
      text: value,
      version: nextVersion,
      dirty: false,
      blocked: false,
    };
    setText(value);
    setConflict(false);
    setStatus("已保存");
  }
  function reconcile(nextVersion: number) {
    state.current.version = nextVersion;
    state.current.blocked = false;
    setConflict(false);
    setStatus("核对完成，待保存");
    cache();
  }
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      void flush();
    },
    [],
  );
  return {
    text,
    change,
    reset,
    reconcile,
    flush,
    status,
    conflict,
    saving,
    recovered: !!recovered.current,
  };
}
