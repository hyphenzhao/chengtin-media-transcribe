import { describe, it, expect, vi } from "vitest";
import { UploadFlow, fingerprint, type UploadState } from "./uploadFlow";
const file = { name: "手机录音.m4a", size: 1234, lastModified: 7 } as File;
function setup() {
  const states: UploadState[] = [];
  const io = {
    create: vi.fn(async () => "new-project"),
    transfer: vi.fn(
      async (_id: string, _file: File, progress: (n: number) => void) => {
        progress(50);
      },
    ),
    changed: (s: UploadState) => states.push(s),
  };
  return { io, states, flow: new UploadFlow(io) };
}
describe("mobile upload destination and recovery", () => {
  it("does not create a project when the picker is cancelled or the file is invalid", async () => {
    const { flow, io } = setup();
    await flow.start(undefined);
    await expect(
      flow.start({ ...file, name: "image.jpg" } as File),
    ).rejects.toThrow("音频或视频");
    await expect(flow.start({ ...file, size: 0 } as File)).rejects.toThrow(
      "不能为空",
    );
    expect(io.create).not.toHaveBeenCalled();
    expect(io.transfer).not.toHaveBeenCalled();
  });
  it("uses the returned project id and ignores duplicate clicks while creation is pending", async () => {
    const { flow, io, states } = setup();
    let resolve!: (id: string) => void;
    io.create.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const pending = flow.start(file);
    await flow.start(file);
    resolve("created-id");
    await pending;
    expect(io.create).toHaveBeenCalledTimes(1);
    expect(io.transfer.mock.calls[0][0]).toBe("created-id");
    expect(states.at(-1)?.phase).toBe("complete");
  });
  it("keeps the original destination when another project is selected", async () => {
    const { flow, io } = setup();
    let selected = "empty-project";
    const work = flow.start(file, selected);
    selected = "different-project";
    await work;
    expect(io.create).not.toHaveBeenCalled();
    expect(io.transfer.mock.calls[0][0]).toBe("empty-project");
  });
  it("retains the failed project and resumes without creating another one", async () => {
    const { flow, io, states } = setup();
    io.transfer.mockRejectedValueOnce(new Error("network interrupted"));
    await expect(flow.start(file)).rejects.toThrow("network interrupted");
    const failed = states.at(-1)!;
    expect(failed.projectId).toBe("new-project");
    expect(failed.phase).toBe("failed");
    await expect(
      flow.start(
        { ...file, name: "other.m4a" } as File,
        failed.projectId,
        failed.fingerprint,
      ),
    ).rejects.toThrow("同一文件");
    await flow.start(file, failed.projectId, fingerprint(file));
    expect(io.create).toHaveBeenCalledTimes(1);
    expect(io.transfer).toHaveBeenCalledTimes(2);
  });
});
