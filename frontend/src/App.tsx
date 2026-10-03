import { confirmAction } from "./Dialogs";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  AudioLines,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  CloudUpload,
  FileAudio2,
  FileText,
  Film,
  Folder,
  FolderOpen,
  GitBranch,
  HardDrive,
  LoaderCircle,
  Maximize2,
  MessageCircle,
  Minimize2,
  MoreHorizontal,
  NotebookPen,
  PanelBottomClose,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { api, type Rec, type Snapshot, size, stamp, uploadFile } from "./api";
import Notes from "./Notes";
import RecordingChat from "./RecordingChat";
import Transcript from "./Transcript";
import Diagram from "./Diagram";
import Settings from "./Settings";

export default function App() {
  const [projects, setProjects] = useState<Rec[]>([]),
    [pid, setPid] = useState(localStorage.getItem("chengtin-project") || ""),
    [snap, setSnap] = useState<Snapshot | null>(null),
    [providers, setProviders] = useState<Rec[]>([]),
    [settingsOpen, setSettingsOpen] = useState(false),
    [toast, setToast] = useState(""),
    [search, setSearch] = useState(""),
    [trash, setTrash] = useState(false),
    [middleTab, setMiddleTab] = useState("source"),
    [textTab, setTextTab] = useState("text"),
    [upperTab, setUpperTab] = useState("notes"),
    [health, setHealth] = useState<any>(null),
    [connected, setConnected] = useState(true),
    [titleEditing, setTitleEditing] = useState(false),
    [titleDraft, setTitleDraft] = useState(""),
    [synopsis, setSynopsis] = useState(""),
    [advanced, setAdvanced] = useState(false),
    [upload, setUpload] = useState<number | null>(null),
    [dragging, setDragging] = useState(false),
    [rightMax, setRightMax] = useState(""),
    [split, setSplit] = useState(
      Number(localStorage.getItem("chengtin-split") || 30),
    ),
    [widths, setWidths] = useState<number[]>(
      JSON.parse(localStorage.getItem("chengtin-widths") || "[18,32]"),
    );
  const file = useRef<HTMLInputElement>(null),
    player = useRef<HTMLMediaElement>(null),
    layout = useRef<HTMLDivElement>(null),
    right = useRef<HTMLElement>(null),
    snapRef = useRef(snap),
    pidRef = useRef(pid),
    refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    synopsisDirty = useRef(false),
    scrollY = useRef(0);
  snapRef.current = snap;
  pidRef.current = pid;
  const error = useCallback((message: string) => setToast(message), []);
  const loadProviders = useCallback(async () => {
    setProviders(await api<Rec[]>("/providers"));
  }, []);
  const loadProjects = useCallback(async () => {
    setProjects(await api<Rec[]>(`/projects?trash=${trash}`));
  }, [trash]);
  const refresh = useCallback(async () => {
    const id = pidRef.current;
    if (!id) return;
    try {
      const data = await api<Snapshot>(`/projects/${id}`);
      if (pidRef.current === id) setSnap(data);
    } catch (e) {
      error((e as Error).message);
    }
  }, [error]);
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void refresh();
      void loadProjects();
    }, 300);
  }, [refresh, loadProjects]);
  useEffect(() => {
    void loadProjects().catch((e) => error(e.message));
    void loadProviders().catch((e) => error(e.message));
    const fetchHealth = () =>
      api("/health")
        .then(setHealth)
        .catch(() => setHealth(null));
    void fetchHealth();
    const t = setInterval(fetchHealth, 15000);
    return () => clearInterval(t);
  }, [loadProjects, loadProviders, error]);
  useEffect(() => {
    if (pid) {
      localStorage.setItem("chengtin-project", pid);
      setSnap(null);
      synopsisDirty.current = false;
      void refresh();
    }
  }, [pid, refresh]);
  useEffect(() => {
    if (!snap) return;
    if (!synopsisDirty.current) setSynopsis(snap.project.data.synopsis || "");
  }, [snap?.project.version]);
  useEffect(() => {
    const source = new EventSource("/api/events");
    source.addEventListener("changed", scheduleRefresh);
    source.addEventListener("deleted", scheduleRefresh);
    source.onopen = () => {
      setConnected(true);
      scheduleRefresh();
    };
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [scheduleRefresh]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 9000);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    localStorage.setItem("chengtin-widths", JSON.stringify(widths));
  }, [widths]);
  useEffect(() => {
    localStorage.setItem("chengtin-split", String(split));
  }, [split]);
  async function create() {
    try {
      const p = await api<Rec>("/projects", "POST");
      setTrash(false);
      setPid(p.id);
      setMiddleTab("source");
      setTextTab("text");
      void loadProjects();
    } catch (e) {
      error((e as Error).message);
    }
  }
  async function patchProject(data: Record<string, any>) {
    const current = snapRef.current;
    if (!current) throw new Error("尚未选择项目");
    const p = await api<Rec>(`/projects/${current.project.id}`, "PATCH", {
      version: current.project.version,
      data,
    });
    if (pidRef.current === current.project.id) {
      setSnap((s) => (s ? { ...s, project: p } : s));
      snapRef.current = { ...current, project: p };
    }
    void loadProjects();
  }
  async function setting(key: string, value: unknown) {
    try {
      await patchProject({
        settings: { ...snapRef.current?.project.data.settings, [key]: value },
      });
    } catch (e) {
      error((e as Error).message);
    }
  }
  async function saveSynopsis() {
    if (!synopsisDirty.current) return;
    try {
      await patchProject({ synopsis });
      synopsisDirty.current = false;
    } catch (e) {
      error((e as Error).message);
    }
  }
  async function attach(f: File) {
    if (!pid) return;
    try {
      setUpload(0);
      await uploadFile(pid, f, "primary", setUpload);
      await refresh();
    } catch (e) {
      error((e as Error).message);
    } finally {
      setUpload(null);
    }
  }
  async function action(path: string, method = "POST") {
    try {
      await api(path, method);
      await refresh();
      await loadProjects();
    } catch (e) {
      error((e as Error).message);
    }
  }
  async function generate(
    kind: string,
    template = "会议纪要",
    instruction = "",
    diagram_type = "auto",
  ) {
    try {
      await api(`/projects/${pid}/generate`, "POST", {
        kind,
        template,
        instruction,
        diagram_type,
      });
      void refresh();
    } catch (e) {
      error((e as Error).message);
    }
  }
  function seek(time: number) {
    setMiddleTab("source");
    setTimeout(() => {
      if (player.current) {
        player.current.currentTime = time;
        void player.current.play().catch(() => {});
      }
    }, 100);
  }
  function resize(e: ReactPointerEvent, axis: "left" | "middle" | "vertical") {
    e.preventDefault();
    const x = e.clientX,
      y = e.clientY,
      start = [...widths],
      initial = split;
    document.body.classList.add("resizing");
    const move = (ev: PointerEvent) => {
      if (axis === "vertical") {
        const height = right.current?.clientHeight || 800;
        setRightMax("");
        setSplit(
          Math.min(
            80,
            Math.max(12, initial + ((ev.clientY - y) / height) * 100),
          ),
        );
      } else {
        const total = layout.current?.clientWidth || 1400,
          delta = ((ev.clientX - x) / total) * 100;
        if (axis === "left") {
          const w = Math.max(14, Math.min(27, start[0] + delta));
          setWidths([w, start[1] + start[0] - w]);
        } else
          setWidths([start[0], Math.max(24, Math.min(44, start[1] + delta))]);
      }
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      document.body.classList.remove("resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
  }
  const primary = snap?.assets.find(
    (a) => a.id === snap.project.data.primary_id,
  );
  const activeJobs =
    snap?.jobs.filter((j) =>
      ["queued", "running", "failed", "cancelled"].includes(j.data.state),
    ) || [];
  const transcribeJob = snap?.jobs.find((j) => j.data.kind === "transcribe");
  const transcriptionRunning =
    transcribeJob && ["queued", "running"].includes(transcribeJob.data.state);
  const graph = snap?.artifacts
    .filter((a) => a.data.kind === "diagram" && a.data.state === "complete")
    .at(-1);
  const graphJob = snap?.jobs.filter((j) => j.data.kind === "diagram").at(-1);
  const diagramBusy =
    snap?.jobs.some(
      (j) =>
        j.data.kind === "diagram" &&
        ["queued", "running"].includes(j.data.state),
    ) || false;
  const settings = snap?.project.data.settings || {};
  return (
    <div
      className="workbench"
      ref={layout}
      style={{
        gridTemplateColumns: `minmax(190px,${widths[0]}%) 5px minmax(300px,${widths[1]}%) 5px minmax(360px,1fr)`,
      }}
    >
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon">
            <AudioLines size={24} />
          </span>
          <div>
            <strong>
              澄听<span>CHENGTIN</span>
            </strong>
            <small>让每一段声音，留下脉络</small>
          </div>
        </div>
        <button className="new-project" onClick={() => void create()}>
          <Plus size={18} />
          新建项目<span>＋</span>
        </button>
        <div className="project-search">
          <Search size={15} />
          <input
            aria-label="搜索项目"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索项目"
          />
        </div>
        <div className="list-heading">
          <span>{trash ? "回收站" : "我的项目"}</span>
          <small>{projects.length}</small>
          <span className="grow" />
          <button
            title={trash ? "返回项目" : "查看回收站"}
            onClick={() => setTrash(!trash)}
          >
            {trash ? <Folder size={14} /> : <Trash2 size={14} />}
          </button>
        </div>
        <div className="project-list">
          {projects
            .filter((p) =>
              p.data.title.toLowerCase().includes(search.toLowerCase()),
            )
            .map((p) => (
              <button
                className={`project-item ${p.id === pid ? "active" : ""}`}
                key={p.id}
                onClick={() => {
                  setPid(p.id);
                  setTitleEditing(false);
                  setMiddleTab("source");
                }}
              >
                <span className="project-file">
                  <FileText size={18} />
                </span>
                <span className="project-label">
                  <strong>{p.data.title}</strong>
                  <small>
                    {new Date(p.updated_at * 1000).toLocaleDateString("zh-CN", {
                      month: "2-digit",
                      day: "2-digit",
                    })}
                    <span>·</span>
                    {p.data.status ||
                      (p.data.primary_id ? "音视频项目" : "待添加素材")}
                  </small>
                </span>
                {p.id === pid && <span className="selected-mark" />}
              </button>
            ))}
          {!projects.length && (
            <div className="sidebar-empty">
              <FolderOpen size={26} />
              <span>{trash ? "回收站为空" : "从一个新项目开始"}</span>
            </div>
          )}
        </div>
        <div className="sidebar-bottom">
          <button onClick={() => setSettingsOpen(true)}>
            <Settings2 size={17} />
            AI 接口设置
            <span className="provider-count">
              {providers.length || "未配置"}
            </span>
          </button>
          <div className="server-card">
            <span
              className={`status-dot ${health?.worker_alive ? "" : "offline"}`}
            />
            <div>
              <strong>
                {health?.worker_alive ? "本地服务运行中" : "正在连接本地服务"}
              </strong>
              <small>.94 · 独立存储与计算</small>
            </div>
            <HardDrive size={18} />
          </div>
          <div className="sidebar-foot">
            <ShieldCheck size={12} /> 本地优先 · 内网共享
          </div>
        </div>
      </aside>
      <div
        className="column-divider"
        onPointerDown={(e) => resize(e, "left")}
      />
      <main className="control-column">
        <header className="project-header">
          <div className="breadcrumb">
            <Folder size={12} />
            工作台
            <ChevronRight size={12} />
            <span>{snap ? "项目空间" : "欢迎使用"}</span>
            <span className="grow" />
            <span className="local-badge">LOCAL</span>
          </div>
          <div className="title-row">
            {titleEditing ? (
              <input
                className="title-input"
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                  if (e.key === "Escape") setTitleEditing(false);
                }}
                onBlur={() => {
                  setTitleEditing(false);
                  if (titleDraft.trim())
                    void patchProject({ title: titleDraft }).catch((e) =>
                      error(e.message),
                    );
                }}
              />
            ) : (
              <h1>{snap?.project.data.title || "新的想法，从这里开始"}</h1>
            )}
            {snap && !snap.project.data.deleted && (
              <>
                <button
                  className="icon-button"
                  title="修改项目名称"
                  onClick={() => {
                    setTitleDraft(snap.project.data.title);
                    setTitleEditing(true);
                  }}
                >
                  <Pencil size={15} />
                </button>
                <button
                  className="icon-button ai-icon"
                  title="AI 生成标题"
                  onClick={() => void generate("title")}
                >
                  <Sparkles size={16} />
                </button>
                <button
                  className="icon-button"
                  title="移入回收站"
                  onClick={async () => {
                    if (
                      await confirmAction(
                        "将此项目移入回收站？正在运行的任务会停止。",
                      )
                    )
                      void action(`/projects/${pid}`, "DELETE");
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </>
            )}
          </div>
          <div className="middle-tabs tabs">
            <button
              className={middleTab === "source" ? "active" : ""}
              onClick={() => setMiddleTab("source")}
            >
              <SlidersHorizontal size={14} />
              素材与控制
            </button>
            <button
              className={`${middleTab === "diagram" ? "active" : ""} ${!graph ? "muted-tab" : ""}`}
              onClick={() => setMiddleTab("diagram")}
            >
              <GitBranch size={14} />
              内容图谱
              {diagramBusy && <LoaderCircle size={12} className="spin" />}
            </button>
          </div>
        </header>
        {snap?.project.data.deleted ? (
          <div className="trash-state">
            <Trash2 size={36} />
            <h3>项目已移入回收站</h3>
            <p>素材、正文和生成结果均保留。</p>
            <button
              className="button primary"
              onClick={() => void action(`/projects/${pid}/restore`)}
            >
              恢复项目
            </button>
            <button
              className="button danger"
              onClick={async () => {
                if (
                  await confirmAction(
                    "永久删除此项目的素材与全部结果？此操作无法撤销。",
                  )
                )
                  void action(`/projects/${pid}/permanent`, "DELETE").then(
                    () => {
                      setPid("");
                      setSnap(null);
                    },
                  );
              }}
            >
              永久删除
            </button>
          </div>
        ) : middleTab === "diagram" ? (
          <Diagram
            item={graph}
            jobError={
              graphJob?.data.state === "failed" ? graphJob.data.error : ""
            }
            busy={diagramBusy}
            stale={
              !!graph &&
              (graph.data.source_revision !==
                snap?.project.data.transcript_revision ||
                (graph.data.extract_version &&
                  snap?.artifacts.find((a) => a.id === graph.data.extract_id)
                    ?.version !== graph.data.extract_version))
            }
            onGenerate={(i, t) => void generate("diagram", "", i, t)}
          />
        ) : (
          <div className="control-scroll">
            {snap &&
              (["title", "synopsis"] as const).map((kind) =>
                snap.project.data[kind + "_suggestion"] ? (
                  <div className="notice" key={kind}>
                    AI {kind === "title" ? "标题" : "梗概"}
                    建议（已保留你的编辑）：
                    <p>{snap.project.data[kind + "_suggestion"]}</p>
                    <button
                      onClick={() =>
                        void patchProject({
                          [kind]: snap.project.data[kind + "_suggestion"],
                        }).catch((e) => error(e.message))
                      }
                    >
                      采用建议
                    </button>
                  </div>
                ) : null,
              )}
            <section className="synopsis-section">
              <div className="section-label">
                <span>内容梗概</span>
                <button
                  disabled={!snap}
                  onClick={() => void generate("synopsis")}
                >
                  <Sparkles size={12} />
                  AI 生成
                </button>
              </div>
              <textarea
                disabled={!snap}
                rows={3}
                value={snap ? synopsis : ""}
                onChange={(e) => {
                  setSynopsis(e.target.value);
                  synopsisDirty.current = true;
                }}
                onBlur={() => void saveSynopsis()}
                placeholder="这段内容讲了什么？写下简短说明，或让 AI 帮你整理。"
              />
              <small>
                {synopsisDirty.current
                  ? "编辑完成后自动保存"
                  : "让重要信息，一眼可见"}
              </small>
            </section>
            <section className="media-section">
              <div className="section-label">
                <span>原始素材</span>
                {primary && (
                  <span className="file-kind">
                    {primary.data.type === "video" ? "VIDEO" : "AUDIO"}
                  </span>
                )}
              </div>
              {primary ? (
                <>
                  <div
                    className={`player-frame ${primary.data.type === "audio" ? "audio-player" : ""}`}
                  >
                    {primary.data.type === "video" ? (
                      <video
                        key={primary.id + primary.data.state}
                        ref={(el) => {
                          player.current = el;
                        }}
                        controls
                        preload="metadata"
                        src={`/api/assets/${primary.id}/media`}
                      />
                    ) : (
                      <>
                        <div className="audio-art">
                          <div className="wave-bars">
                            {Array.from({ length: 32 }, (_, i) => (
                              <i
                                key={i}
                                style={{
                                  height: `${18 + Math.sin(i * 1.3) ** 2 * 48}%`,
                                }}
                              />
                            ))}
                          </div>
                          <span>每一个声音，都有迹可循</span>
                        </div>
                        <audio
                          key={primary.id + primary.data.state}
                          ref={(el) => {
                            player.current = el;
                          }}
                          controls
                          src={`/api/assets/${primary.id}/media`}
                        />
                      </>
                    )}
                    {primary.data.state === "preparing" && (
                      <div className="player-notice">
                        <LoaderCircle className="spin" size={13} />
                        正在准备兼容播放版本
                      </div>
                    )}
                  </div>
                  <div className="media-info">
                    <span className="file-icon">
                      {primary.data.type === "video" ? (
                        <Film size={18} />
                      ) : (
                        <FileAudio2 size={18} />
                      )}
                    </span>
                    <div>
                      <strong title={primary.data.name}>
                        {primary.data.name}
                      </strong>
                      <small>
                        {stamp(primary.data.duration)}
                        <span>·</span>
                        {size(primary.data.size)}
                      </small>
                    </div>
                    <a
                      href={`/api/assets/${primary.id}/media?original=true`}
                      target="_blank"
                      rel="noreferrer"
                      title="打开原始文件"
                    >
                      <MoreHorizontal size={17} />
                    </a>
                  </div>
                </>
              ) : (
                <div
                  className={`upload-area ${dragging ? "dragging" : ""}`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragging(false);
                    const f = e.dataTransfer.files[0];
                    if (f && snap) void attach(f);
                  }}
                >
                  <span className="upload-symbol">
                    {upload !== null ? (
                      <LoaderCircle size={28} className="spin" />
                    ) : (
                      <CloudUpload size={30} />
                    )}
                  </span>
                  <h3>
                    {upload !== null
                      ? `正在上传 · ${upload}%`
                      : "把声音，变成清晰的文字"}
                  </h3>
                  <p>
                    {upload !== null ? (
                      "支持断点续传，请保持页面开启"
                    ) : (
                      <>
                        将音视频拖放到这里
                        <br />
                        或从电脑中选择文件
                      </>
                    )}
                  </p>
                  {upload !== null ? (
                    <div className="progress-track">
                      <div style={{ width: upload + "%" }} />
                    </div>
                  ) : (
                    <button
                      className="button secondary"
                      onClick={() => {
                        if (!snap) void create();
                        else file.current?.click();
                      }}
                    >
                      <Plus size={15} />
                      {snap ? "选择音视频文件" : "先新建一个项目"}
                    </button>
                  )}
                  <small>MP4 / MOV / MP3 / WAV 等 · 最大 20 GB</small>
                  <input
                    ref={file}
                    type="file"
                    hidden
                    accept=".mp4,.mov,.mkv,.webm,.avi,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void attach(f);
                      e.target.value = "";
                    }}
                  />
                </div>
              )}
            </section>
            <section className="transcription-controls">
              <button
                className="button primary start-button"
                disabled={
                  !primary ||
                  !!transcriptionRunning ||
                  transcribeJob?.data.state === "complete"
                }
                onClick={() => {
                  setTextTab("text");
                  if (
                    transcribeJob &&
                    ["failed", "cancelled"].includes(transcribeJob.data.state)
                  )
                    void action(`/jobs/${transcribeJob.id}/retry`);
                  else void action(`/projects/${pid}/transcribe`);
                }}
              >
                {transcriptionRunning ? (
                  <LoaderCircle size={17} className="spin" />
                ) : transcribeJob?.data.state === "complete" ? (
                  <Check size={18} />
                ) : (
                  <Play size={17} fill="currentColor" />
                )}
                {transcriptionRunning
                  ? "正在转录"
                  : transcribeJob?.data.state === "complete"
                    ? "转录已完成"
                    : transcribeJob
                      ? "继续转录"
                      : primary && !primary.data.has_audio
                        ? "提取画面文字"
                        : "开始转录"}
              </button>
              <div className="start-hint">
                <ShieldCheck size={12} />
                音视频与画面均在本地处理
              </div>
              <div className="ai-setting-row">
                <div className="ai-setting-label">
                  <span className="ai-setting-icon">
                    <Sparkles size={16} />
                  </span>
                  <div>
                    <strong>AI 上下文修订</strong>
                    <small>纠正识别错误，保留原意</small>
                  </div>
                </div>
                <button
                  role="switch"
                  aria-label="AI 上下文修订"
                  aria-checked={!!settings.ai_enabled}
                  className={`switch ${settings.ai_enabled ? "on" : ""}`}
                  disabled={!snap || !!transcriptionRunning}
                  onClick={() =>
                    void setting("ai_enabled", !settings.ai_enabled)
                  }
                >
                  <span />
                </button>
              </div>
              <select
                aria-label="项目 AI 接口"
                className="provider-select"
                disabled={!snap || !!transcriptionRunning}
                value={settings.provider_id || ""}
                onChange={(e) => void setting("provider_id", e.target.value)}
              >
                <option value="">
                  {providers.length ? "选择 AI 接口" : "尚未配置 AI 接口"}
                </option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.data.name} · {p.data.model}
                  </option>
                ))}
              </select>
              {!providers.length && (
                <button
                  className="text-link"
                  onClick={() => setSettingsOpen(true)}
                >
                  配置你的 AI 接口 <ChevronRight size={12} />
                </button>
              )}
              <button
                className="advanced-toggle"
                onClick={() => setAdvanced(!advanced)}
              >
                <SlidersHorizontal size={13} />
                转录设置
                <span className="grow" />
                {advanced ? (
                  <ChevronDown size={14} />
                ) : (
                  <ChevronRight size={14} />
                )}
              </button>
              {advanced && snap && (
                <div className="advanced-settings">
                  <label>
                    主要语言
                    <select
                      disabled={!!transcriptionRunning}
                      value={settings.language}
                      onChange={(e) => void setting("language", e.target.value)}
                    >
                      <option value="auto">中英自动识别</option>
                      <option value="zh">中文</option>
                      <option value="en">英文</option>
                    </select>
                  </label>
                  <label>
                    AI 分段字数
                    <input
                      disabled={!!transcriptionRunning}
                      type="number"
                      min="100"
                      max="1500"
                      defaultValue={settings.threshold}
                      onBlur={(e) =>
                        void setting("threshold", Number(e.target.value))
                      }
                    />
                  </label>
                  <label className="checkbox-label">
                    <input
                      disabled={!!transcriptionRunning}
                      type="checkbox"
                      checked={!!settings.ocr}
                      onChange={(e) => void setting("ocr", e.target.checked)}
                    />
                    本地识别视频画面文字
                  </label>
                  <label className="checkbox-label">
                    <input
                      disabled={!!transcriptionRunning}
                      type="checkbox"
                      checked={!!settings.diarization}
                      onChange={(e) =>
                        void setting("diarization", e.target.checked)
                      }
                    />
                    自动区分说话人
                  </label>
                  <label>
                    说话人分组
                    <select
                      disabled={!!transcriptionRunning}
                      value={settings.speaker_threshold ?? 0.8}
                      onChange={(e) =>
                        void setting(
                          "speaker_threshold",
                          Number(e.target.value),
                        )
                      }
                    >
                      <option value={0.5}>区分更细</option>
                      <option value={0.8}>均衡</option>
                      <option value={1.0}>减少碎片</option>
                    </select>
                  </label>
                  <label>
                    修订提示词
                    <textarea
                      disabled={!!transcriptionRunning}
                      rows={4}
                      defaultValue={settings.system_prompt}
                      onBlur={(e) =>
                        void setting("system_prompt", e.target.value)
                      }
                    />
                  </label>
                </div>
              )}
            </section>
            {activeJobs.length > 0 && (
              <section className="jobs">
                <div className="section-label">
                  <span>处理进度</span>
                  <small>
                    {
                      activeJobs.filter((j) => j.data.state === "running")
                        .length
                    }{" "}
                    个运行中
                  </small>
                </div>
                {activeJobs.slice(-12).map((j) => (
                  <div className={`job ${j.data.state}`} key={j.id}>
                    <div>
                      <span>
                        {j.data.state === "running" ? (
                          <LoaderCircle size={12} className="spin" />
                        ) : j.data.state === "failed" ? (
                          <CircleHelp size={12} />
                        ) : (
                          <span className="job-dot" />
                        )}
                        {j.data.stage}
                      </span>
                      {["queued", "running"].includes(j.data.state) ? (
                        <button
                          title="停止任务"
                          onClick={() => void action(`/jobs/${j.id}/cancel`)}
                        >
                          <Square size={11} />
                        </button>
                      ) : (
                        <button
                          onClick={() => void action(`/jobs/${j.id}/retry`)}
                        >
                          <RotateCcw size={12} />
                          重试
                        </button>
                      )}
                    </div>
                    {j.data.state === "running" && (
                      <div className="progress-track">
                        <div
                          style={{
                            width: Math.max(2, j.data.progress * 100) + "%",
                          }}
                        />
                      </div>
                    )}
                    {j.data.error && <p>{j.data.error}</p>}
                  </div>
                ))}
              </section>
            )}
            <div className="workflow-note">
              <div>
                <span>01</span>本地识别
              </div>
              <ChevronRight size={10} />
              <div>
                <span>02</span>上下文修订
              </div>
              <ChevronRight size={10} />
              <div>
                <span>03</span>提取与图谱
              </div>
            </div>
          </div>
        )}
        <footer className="control-footer">
          <span className={`status-dot ${connected ? "" : "offline"}`} />
          {connected ? "实时更新已连接" : "连接中断，正在重连"}
          <span className="grow" />
          <span>澄听 1.0</span>
        </footer>
      </main>
      <div
        className="column-divider"
        onPointerDown={(e) => resize(e, "middle")}
      />
      <section
        className="right-column"
        ref={right}
        style={{
          gridTemplateRows:
            rightMax === "notes"
              ? "minmax(0,1fr) 5px 45px"
              : rightMax === "text"
                ? "48px 5px minmax(0,1fr)"
                : `minmax(110px,${split}%) 5px minmax(200px,1fr)`,
        }}
      >
        <section className="notes-panel">
          <header className="panel-heading">
            <div
              className="tabs upper-tabs"
              role="tablist"
              aria-label="补充内容与录音问答"
            >
              <button
                role="tab"
                aria-selected={upperTab === "notes"}
                className={upperTab === "notes" ? "active" : ""}
                onClick={() => setUpperTab("notes")}
              >
                <NotebookPen size={15} />
                补充内容
              </button>
              <button
                role="tab"
                aria-selected={upperTab === "chat"}
                className={`${upperTab === "chat" ? "active" : ""} ${!snap?.artifacts.some((a) => a.data.kind === "extract" && a.data.state === "complete") ? "muted-tab" : ""}`}
                onClick={() => setUpperTab("chat")}
              >
                <MessageCircle size={15} />
                内容问答
              </button>
            </div>
            <span className="grow" />
            <button
              className="icon-button"
              title={
                rightMax === "notes"
                  ? "恢复布局"
                  : upperTab === "chat"
                    ? "放大内容问答"
                    : "放大补充内容"
              }
              onClick={() => setRightMax(rightMax === "notes" ? "" : "notes")}
            >
              {rightMax === "notes" ? (
                <Minimize2 size={14} />
              ) : (
                <Maximize2 size={14} />
              )}
            </button>
            <button
              className="icon-button"
              title={upperTab === "chat" ? "收起内容问答" : "收起补充内容"}
              onClick={() => setRightMax(rightMax === "text" ? "" : "text")}
            >
              <PanelBottomClose size={15} />
            </button>
          </header>
          {rightMax !== "text" &&
            (snap ? (
              upperTab === "chat" ? (
                <RecordingChat
                  key={pid}
                  snap={snap}
                  seek={seek}
                  refresh={() => void refresh()}
                  error={error}
                  onExtract={() => {
                    setTextTab("extract");
                    setRightMax("");
                  }}
                  onConfigure={() => setSettingsOpen(true)}
                />
              ) : (
                <Notes
                  key={pid}
                  pid={pid}
                  value={snap.project.data.notes}
                  version={snap.project.data.notes_version || 0}
                  assets={snap.assets.filter(
                    (a) => a.data.role === "supplement",
                  )}
                  refresh={() => void refresh()}
                  error={error}
                />
              )
            ) : (
              <div className="notes-placeholder">
                {upperTab === "chat"
                  ? "完成内容提取后，可在这里询问录音内容。"
                  : "为想法留一处空间。"}
                <br />
                <span>新建项目后，可以写笔记、放图片、添加音视频。</span>
              </div>
            ))}
        </section>
        <div
          className="row-divider"
          onPointerDown={(e) => resize(e, "vertical")}
        >
          <span />
        </div>
        <section className="text-panel">
          <button
            className="text-max icon-button"
            title={rightMax === "text" ? "恢复布局" : "放大正文"}
            onClick={() => setRightMax(rightMax === "text" ? "" : "text")}
          >
            {rightMax === "text" ? (
              <Minimize2 size={14} />
            ) : (
              <Maximize2 size={14} />
            )}
          </button>
          {rightMax === "notes" ? (
            <div className="collapsed-label">
              <FileText size={15} />
              转录正文
            </div>
          ) : snap ? (
            <Transcript
              key={pid}
              snap={snap}
              seek={seek}
              refresh={() => void refresh()}
              error={error}
              tab={textTab}
              setTab={setTextTab}
              onGenerate={(k, t, i) => void generate(k, t, i)}
            />
          ) : (
            <>
              <div className="transcript-tabs">
                <div className="tabs">
                  <button className="active muted-tab">
                    <FileText size={15} />
                    转录正文
                  </button>
                  <button className="muted-tab">
                    <Sparkles size={15} />
                    内容提取
                  </button>
                </div>
              </div>
              <div className="transcript-empty">
                <div className="paper-illustration">
                  <div />
                  <div />
                  <div />
                  <span>
                    <Sparkles size={16} />
                  </span>
                </div>
                <h3>每一句，都值得被记录</h3>
                <p>
                  新建一个项目，上传音视频。
                  <br />
                  让声音成为可编辑、可回溯的文字。
                </p>
                <div className="state-legend">
                  <span>
                    <i className="gray-dot" />
                    本地转写
                  </span>
                  <ChevronRight size={12} />
                  <span>
                    <i className="orange-dot" />
                    AI 修订
                  </span>
                  <ChevronRight size={12} />
                  <span>
                    <i className="blue-dot" />
                    确定正文
                  </span>
                </div>
              </div>
            </>
          )}
        </section>
      </section>
      {settingsOpen && (
        <Settings
          providers={providers}
          refresh={loadProviders}
          close={() => setSettingsOpen(false)}
          error={error}
        />
      )}
      {toast && (
        <div className="toast" role="alert">
          <CircleHelp size={18} />
          <span>{toast}</span>
          <button onClick={() => setToast("")}>
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
