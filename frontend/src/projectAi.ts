import { api, type Rec, type Snapshot } from "./api";

export class ProjectAiSetupError extends Error {
  constructor(
    message: string,
    public destination: "configure" | "select",
  ) {
    super(message);
  }
}

export async function generateForProject(
  projectId: string,
  request: {
    kind: string;
    template: string;
    instruction: string;
    diagram_type: string;
  },
  providersLoaded?: (providers: Rec[]) => void,
) {
  // Read fresh state so a saved interface in another tab is immediately usable.
  const [snapshot, providers] = await Promise.all([
    api<Snapshot>(`/projects/${projectId}`),
    api<Rec[]>("/providers"),
  ]);
  providersLoaded?.(providers);
  const project = snapshot.project;
  const settings = project.data.settings;
  const selected = settings.provider_id;
  if (!providers.some((p) => p.id === selected)) {
    if (!providers.length)
      throw new ProjectAiSetupError(
        "尚未保存 AI 接口，请先添加配置。",
        "configure",
      );
    if (selected || providers.length > 1)
      throw new ProjectAiSetupError(
        selected
          ? "当前项目原来选择的 AI 接口已不可用，请从已保存的接口中重新选择。"
          : `已保存 ${providers.length} 个 AI 接口，请为当前项目选择一个，无需重新填写配置。`,
        "select",
      );
    // Selecting an interface does not enable automatic transcript revision.
    await api(`/projects/${projectId}`, "PATCH", {
      version: project.version,
      data: { settings: { ...settings, provider_id: providers[0].id } },
    });
  }
  return api(`/projects/${projectId}/generate`, "POST", request);
}
