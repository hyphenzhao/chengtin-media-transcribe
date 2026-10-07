export type UploadState = {
  projectId: string;
  name: string;
  fingerprint: string;
  progress: number;
  phase: "creating" | "uploading" | "failed" | "complete";
  error?: string;
};
export const mediaAccept =
  "audio/*,video/*,.mp4,.mov,.mkv,.webm,.avi,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus";
export const fingerprint = (file: File) =>
  `${file.name}|${file.size}|${file.lastModified}`;
export function validateMedia(file: File) {
  if (
    !/\.(mp4|mov|mkv|webm|avi|mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(file.name)
  )
    throw new Error("请选择支持的音频或视频文件，例如 M4A、MP3、MP4、MOV。");
  if (!file.size || file.size > 20 * 1024 ** 3)
    throw new Error("文件不能为空，且单个文件不能超过 20GB。");
}

/** Capture the destination before any asynchronous work; navigation cannot change it. */
export class UploadFlow {
  busy = false;
  constructor(
    private io: {
      create: () => Promise<string>;
      transfer: (
        id: string,
        file: File,
        progress: (n: number) => void,
      ) => Promise<unknown>;
      changed: (state: UploadState) => void;
    },
  ) {}
  async start(file: File | undefined, projectId?: string, expected?: string) {
    if (!file || this.busy) return;
    validateMedia(file);
    if (expected && fingerprint(file) !== expected)
      throw new Error(
        "请重新选择之前上传的同一文件；要处理另一份素材，请使用「新转写」。",
      );
    this.busy = true;
    let state: UploadState = {
      projectId: projectId || "",
      name: file.name,
      fingerprint: fingerprint(file),
      progress: 0,
      phase: projectId ? "uploading" : "creating",
    };
    const publish = (patch: Partial<UploadState>) => {
      state = { ...state, ...patch };
      this.io.changed(state);
    };
    try {
      publish({});
      const id = projectId || (await this.io.create());
      publish({ projectId: id, phase: "uploading" });
      await this.io.transfer(id, file, (progress) => publish({ progress }));
      publish({ phase: "complete", progress: 100 });
      return id;
    } catch (error) {
      publish({ phase: "failed", error: (error as Error).message });
      throw error;
    } finally {
      this.busy = false;
    }
  }
}
