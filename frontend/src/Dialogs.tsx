import { useEffect, useRef, useState } from "react";
type Request = {
  message: string;
  initial?: string;
  resolve: (value: string | null) => void;
};
let show: ((request: Request) => void) | undefined;
export async function confirmAction(message: string) {
  return (
    (await new Promise<string | null>((resolve) =>
      show?.({ message, resolve }),
    )) !== null
  );
}
export function promptText(message: string, initial: string) {
  return new Promise<string | null>((resolve) =>
    show?.({ message, initial, resolve }),
  );
}
export default function Dialogs() {
  const [request, setRequest] = useState<Request | null>(null),
    [value, setValue] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    show = (r) => {
      setValue(r.initial || "");
      setRequest(r);
    };
    return () => {
      show = undefined;
    };
  }, []);
  useEffect(() => {
    if (request)
      box.current?.querySelector<HTMLElement>("input,button")?.focus();
  }, [request]);
  if (!request) return null;
  function finish(value: string | null) {
    request!.resolve(value);
    setRequest(null);
  }
  return (
    <div className="modal-backdrop" style={{ zIndex: 200 }}>
      <div
        className="confirm-modal"
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label="确认操作"
        onKeyDown={(e) => {
          if (e.key === "Escape") finish(null);
          if (e.key === "Tab") {
            const items = Array.from(
              box.current!.querySelectorAll<HTMLElement>("input,button"),
            );
            const current = items.indexOf(
              document.activeElement as HTMLElement,
            );
            e.preventDefault();
            items[
              (current + (e.shiftKey ? -1 : 1) + items.length) % items.length
            ]?.focus();
          }
        }}
      >
        <h2>{request.initial !== undefined ? "修改名称" : "请确认"}</h2>
        <p>{request.message}</p>
        {request.initial !== undefined && (
          <input
            aria-label="新的名称"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") finish(value);
            }}
          />
        )}
        <div className="form-actions">
          <button className="button secondary" onClick={() => finish(null)}>
            取消
          </button>
          <button
            className="button primary"
            onClick={() =>
              finish(request.initial === undefined ? "yes" : value)
            }
          >
            确认
          </button>
        </div>
      </div>
    </div>
  );
}
