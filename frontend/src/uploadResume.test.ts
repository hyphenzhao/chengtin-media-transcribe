import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadFile } from "./api";

const file = new File(["audio-data"], "audio.m4a", { lastModified: 123 });
const session = (received: Record<string, string> = {}) => ({
  id: "upload-1",
  project_id: "project-1",
  data: {
    fingerprint: `${file.name}|${file.size}|${file.lastModified}`,
    role: "primary",
    count: 2,
    chunk_size: 5,
    received,
  },
});
const response = (data: unknown) => new Response(JSON.stringify(data));
beforeEach(() => {
  const cache = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => cache.get(key) || null,
    setItem: (key: string, value: string) => cache.set(key, value),
    removeItem: (key: string) => cache.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
describe("upload session recovery", () => {
  it("skips previously received chunks when resuming", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response(session({ "0": "hash" })))
      .mockResolvedValueOnce(response({}))
      .mockResolvedValueOnce(response({ id: "asset-1" }));
    vi.stubGlobal("fetch", fetch);
    await uploadFile("project-1", file, "primary", vi.fn());
    expect(fetch.mock.calls.map((c) => c[0])).toEqual([
      "/api/projects/project-1/uploads",
      "/api/uploads/upload-1/chunks/1",
      "/api/uploads/upload-1/complete",
    ]);
  });
  it("recovers a lost completion response without starting a second upload", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response(session({ "0": "hash", "1": "hash" })))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(response(session({ "0": "hash", "1": "hash" })))
      .mockResolvedValueOnce(response({ id: "asset-1" }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      uploadFile("project-1", file, "primary", vi.fn()),
    ).rejects.toThrow("offline");
    await expect(
      uploadFile("project-1", file, "primary", vi.fn()),
    ).resolves.toEqual({ id: "asset-1" });
    expect(fetch.mock.calls.map((c) => c[0])).toEqual([
      "/api/projects/project-1/uploads",
      "/api/uploads/upload-1/complete",
      "/api/uploads/upload-1",
      "/api/uploads/upload-1/complete",
    ]);
    expect(
      localStorage.getItem("chengtin-upload-session:project-1:primary"),
    ).toBeNull();
  });
});
