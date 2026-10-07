import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { generateForProject } from "./projectAi";
vi.mock("./api", () => ({ api: vi.fn() }));
const call = vi.mocked(api);
const request = {
  kind: "title",
  template: "会议纪要",
  instruction: "",
  diagram_type: "auto",
};
const settings = {
  provider_id: "",
  ai_enabled: false,
  ocr: true,
  language: "auto",
};
function setup(selected: string, ids: string[]) {
  call.mockResolvedValueOnce({
    project: {
      version: 7,
      data: { settings: { ...settings, provider_id: selected } },
    },
  });
  call.mockResolvedValueOnce(ids.map((id) => ({ id })));
}
beforeEach(() => vi.resetAllMocks());
describe("AI generation uses saved project interfaces", () => {
  it("selects the sole saved interface before generating without enabling AI revision", async () => {
    setup("", ["deepseek"]);
    await generateForProject("project", request);
    expect(call.mock.calls).toEqual([
      ["/projects/project"],
      ["/providers"],
      [
        "/projects/project",
        "PATCH",
        {
          version: 7,
          data: { settings: { ...settings, provider_id: "deepseek" } },
        },
      ],
      ["/projects/project/generate", "POST", request],
    ]);
  });
  it("keeps an explicit project selection when multiple interfaces exist", async () => {
    setup("chosen", ["other", "chosen"]);
    await generateForProject("project", request);
    expect(call).toHaveBeenCalledTimes(3);
    expect(call).toHaveBeenLastCalledWith(
      "/projects/project/generate",
      "POST",
      request,
    );
  });
  it("distinguishes multiple saved interfaces from missing configuration", async () => {
    setup("", ["a", "b"]);
    await expect(generateForProject("project", request)).rejects.toMatchObject({
      destination: "select",
      message: expect.stringContaining("无需重新填写配置"),
    });
    expect(call).toHaveBeenCalledTimes(2);
  });
  it("opens configuration only when no saved interfaces exist", async () => {
    setup("", []);
    await expect(generateForProject("project", request)).rejects.toMatchObject({
      destination: "configure",
    });
    expect(call).toHaveBeenCalledTimes(2);
  });
  it("does not silently replace an explicitly selected but removed interface", async () => {
    setup("removed", ["replacement"]);
    await expect(generateForProject("project", request)).rejects.toMatchObject({
      destination: "select",
    });
    expect(call).toHaveBeenCalledTimes(2);
  });
  it("does not enqueue generation when saving the selection has a version conflict", async () => {
    setup("", ["deepseek"]);
    call.mockRejectedValueOnce(new Error("版本冲突"));
    await expect(
      generateForProject("original-project", request),
    ).rejects.toThrow("版本冲突");
    expect(call).toHaveBeenCalledTimes(3);
    expect(call.mock.calls[2][0]).toBe("/projects/original-project");
  });
});
