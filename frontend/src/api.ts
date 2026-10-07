export type Rec = {
  id: string;
  kind: string;
  project_id: string;
  version: number;
  data: Record<string, any>;
  created_at: number;
  updated_at: number;
};
export type Snapshot = {
  project: Rec;
  assets: Rec[];
  blocks: Rec[];
  segments: Rec[];
  speakers: Rec[];
  evidences: Rec[];
  jobs: Rec[];
  artifacts: Rec[];
  chats: Rec[];
  event_cursor: number;
};
export async function api<T = any>(
  path: string,
  method = "GET",
  data?: unknown,
): Promise<T> {
  const res = await fetch("/api" + path, {
    method,
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (!res.ok) {
    let msg = "请求失败";
    try {
      const body = await res.json();
      msg =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {
      msg = `请求失败 (${res.status})`;
    }
    throw new Error(msg);
  }
  return res.json();
}
export async function uploadFile(
  pid: string,
  file: File,
  role: string,
  progress: (n: number) => void,
) {
  const fingerprint = `${file.name}|${file.size}|${file.lastModified}`;
  const cacheKey = `chengtin-upload-session:${pid}:${role}`;
  let saved: { id: string; fingerprint: string } | undefined;
  try {
    saved = JSON.parse(localStorage.getItem(cacheKey) || "null");
  } catch {
    /* Storage may be unavailable in private browsing. */
  }
  // Retain the upload ID across refreshes, including a lost completion response.
  const u: Rec =
    saved?.fingerprint === fingerprint
      ? await api(`/uploads/${saved.id}`)
      : await api(`/projects/${pid}/uploads`, "POST", {
          name: file.name,
          size: file.size,
          role,
          fingerprint,
        });
  if (
    u.project_id !== pid ||
    u.data.fingerprint !== fingerprint ||
    u.data.role !== role
  )
    throw new Error("上传记录与所选文件不一致，请重新选择文件。");
  try {
    localStorage.setItem(cacheKey, JSON.stringify({ id: u.id, fingerprint }));
  } catch {
    /* Upload still works without storage. */
  }
  const d = u.data;
  for (let i = 0; i < d.count; i++) {
    if (!(String(i) in d.received)) {
      let error: Error | undefined;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const res = await fetch(`/api/uploads/${u.id}/chunks/${i}`, {
            method: "PUT",
            body: file.slice(
              i * d.chunk_size,
              Math.min((i + 1) * d.chunk_size, file.size),
            ),
          });
          if (!res.ok) {
            const b = await res.json();
            throw new Error(b.detail || "分块上传失败");
          }
          error = undefined;
          break;
        } catch (e) {
          error = e as Error;
          await new Promise((r) => setTimeout(r, 600 * (attempt + 1)));
        }
      }
      if (error)
        throw new Error(error.message + "。重新选择同一文件即可继续上传。");
    }
    progress(Math.min(99, Math.round(((i + 1) / d.count) * 99)));
  }
  const result: Rec = await api(`/uploads/${u.id}/complete`, "POST");
  try {
    localStorage.removeItem(cacheKey);
  } catch {
    /* Optional cache. */
  }
  progress(100);
  return result;
}
export function stamp(t: number) {
  const s = Math.max(0, Math.floor(t));
  return `${Math.floor(s / 3600)
    .toString()
    .padStart(2, "0")}:${Math.floor((s % 3600) / 60)
    .toString()
    .padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}
export function size(bytes: number) {
  return bytes > 1024 ** 3
    ? (bytes / 1024 ** 3).toFixed(1) + " GB"
    : (bytes / 1024 ** 2).toFixed(1) + " MB";
}
export function downloadBlob(content: BlobPart, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function downloadFile(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  link.remove();
}
