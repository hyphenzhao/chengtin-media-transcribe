import { useAutosave } from "./useAutosave";
import { confirmAction, promptText } from "./Dialogs";
import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  FileText,
  History,
  LoaderCircle,
  Pencil,
  RotateCcw,
  Sparkles,
  UserRound,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Rec, type Snapshot, stamp } from "./api";

function Block({
  b,
  snap,
  seek,
  refresh,
  error,
}: {
  b: Rec;
  snap: Snapshot;
  seek: (n: number) => void;
  refresh: () => void;
  error: (s: string) => void;
}) {
  const d = b.data;
  const editor = useAutosave(
    b.id,
    `/blocks/${b.id}`,
    d.text ?? d.raw,
    b.version,
    refresh,
    error,
  );
  const [editing, setEditing] = useState(editor.recovered),
    [audit, setAudit] = useState(false),
    [history, setHistory] = useState<Rec[]>([]),
    [rawSegments, setRawSegments] = useState<Rec[]>([]);
  const run = async (fn: () => Promise<any>) => {
    try {
      await fn();
      refresh();
    } catch (e) {
      error((e as Error).message);
    }
  };
  async function save() {
    if (await editor.flush()) setEditing(false);
  }
  async function toggleAudit() {
    setAudit(!audit);
    if (!audit) {
      try {
        const [h, s] = await Promise.all([
          api<Rec[]>(`/blocks/${b.id}/history`),
          Promise.all(
            d.segment_ids.map((id: string) => api<Rec>(`/segments/${id}`)),
          ),
        ]);
        setHistory(h);
        setRawSegments(s);
      } catch (e) {
        error((e as Error).message);
      }
    }
  }
  const processing = d.state === "queued" || d.state === "revising";
  const stateText =
    d.state === "accumulating"
      ? "正在转写"
      : d.state === "queued"
        ? "等待 AI 修订"
        : d.state === "revising"
          ? "AI 正在修订"
          : d.state === "ai_failed"
            ? "AI 未完成"
            : d.manual
              ? "已人工编辑"
              : d.ai_revised
                ? "AI 已修订"
                : "本地转写";
  return (
    <article className={`transcript-block ${d.state}`}>
      <div className="block-meta">
        <button className="timecode" onClick={() => seek(d.start)}>
          <Clock3 size={11} />
          {stamp(d.start)}
        </button>
        <span className="speaker-icon">
          <UserRound size={12} />
        </span>
        <select
          className="speaker-select"
          aria-label="说话人"
          value={d.speaker_id || ""}
          onChange={(e) =>
            void run(() =>
              api(`/blocks/${b.id}`, "PATCH", {
                version: b.version,
                data: { speaker_id: e.target.value },
              }),
            )
          }
        >
          <option value="">
            {snap.speakers.length
              ? "未分配"
              : snap.project.data.settings.diarization
                ? "待分析"
                : "未启用区分"}
          </option>
          {snap.speakers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.data.name}
            </option>
          ))}
        </select>
        {d.speaker_mixed && (
          <span
            className="tiny-tag"
            title="此段内含多次发言，展开核对可查看说话人时段"
          >
            多位发言
          </span>
        )}
        <span className="grow" />
        <span className="block-state">
          {processing ? (
            <LoaderCircle size={11} className="spin" />
          ) : d.state === "final" ? (
            <Check size={11} />
          ) : (
            <span className="pulse-dot" />
          )}
          {stateText}
        </span>
      </div>
      {editing ? (
        <div className="block-edit">
          <textarea
            autoFocus
            value={editor.text}
            onChange={(e) => editor.change(e.target.value)}
            onBlur={() => void editor.flush()}
            rows={Math.max(4, Math.min(12, Math.ceil(editor.text.length / 45)))}
          />
          <div className="inline-actions">
            <small>{editor.status}</small>
            {editor.conflict && (
              <button
                onClick={async () => {
                  if (
                    await confirmAction(
                      "服务器最新正文：\n" +
                        (d.text ?? d.raw) +
                        "\n\n保留当前草稿继续编辑，并使用这个最新版本保存？",
                    )
                  ) {
                    editor.reconcile(b.version);
                  }
                }}
              >
                核对最新版本
              </button>
            )}
            <button onClick={() => setEditing(false)}>
              {" "}
              <X size={13} />
              收起
            </button>
            <button
              className="primary-small"
              disabled={editor.saving || editor.conflict}
              onClick={() => void save()}
            >
              <Check size={13} />
              {editor.saving ? "保存中" : "保存并完成"}
            </button>
          </div>
        </div>
      ) : (
        <div className="block-text">
          {processing
            ? d.ai_draft || d.raw
            : d.state === "accumulating"
              ? d.raw
              : (d.text ?? d.raw)}
          {d.state === "accumulating" && <span className="cursor" />}
        </div>
      )}
      {d.uncertain && (
        <div className="uncertain">存在模型分歧或画面文字，建议回听核对</div>
      )}
      {d.suggestion && (
        <details className="suggestion">
          <summary>有一份 AI 修订建议，人工编辑已保留</summary>
          <p>{d.suggestion}</p>
          <button
            onClick={() =>
              void run(() =>
                api(`/blocks/${b.id}/adopt-suggestion`, "POST", {
                  version: b.version,
                  data: {},
                }),
              )
            }
          >
            采用这份建议
          </button>
        </details>
      )}
      {d.state !== "accumulating" && (
        <div className="block-actions">
          <button onClick={() => void toggleAudit()}>
            {audit ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            原文与核对
          </button>
          {!processing && !editing && (
            <button
              onClick={() => {
                editor.reset(d.text ?? d.raw, b.version);
                setEditing(true);
              }}
            >
              <Pencil size={12} />
              编辑
            </button>
          )}
          {!processing && (
            <button
              onClick={() =>
                void run(() => api(`/blocks/${b.id}/revise`, "POST"))
              }
            >
              <Sparkles size={12} />
              {d.state === "ai_failed" ? "重试修订" : "重新修订"}
            </button>
          )}
          {d.state === "ai_failed" && (
            <button
              onClick={() =>
                void run(() =>
                  api(`/blocks/${b.id}/adopt-raw`, "POST", {
                    version: b.version,
                    data: {},
                  }),
                )
              }
            >
              采用原文
            </button>
          )}
        </div>
      )}
      {audit && (
        <div className="audit">
          <h5>原始识别</h5>
          <p>{d.raw}</p>
          {rawSegments.map((s) => (
            <details key={s.id}>
              <summary>{stamp(s.data.start)} · 两模型原始结果</summary>
              <p>
                <strong>中文模型：</strong>
                {s.data.sensevoice?.text || "无结果"}
              </p>
              <p>
                <strong>英文模型：</strong>
                {s.data.whisper?.text || "无结果"}
              </p>
            </details>
          ))}
          {d.speaker_turns?.length > 0 && (
            <details>
              <summary>本段说话人时段</summary>
              {d.speaker_turns.map((t: any, i: number) => (
                <p key={i}>
                  {stamp(t.start)}–{stamp(t.end)} ·{" "}
                  {snap.speakers.find((s) => s.id === t.speaker_id)?.data
                    .name || "未知"}
                </p>
              ))}
            </details>
          )}
          {snap.evidences
            .filter((e) => d.evidence_ids?.includes(e.id))
            .map((e) => (
              <div className="evidence" key={e.id}>
                <a
                  href={`/api/evidence/${e.id}/image`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    loading="lazy"
                    src={`/api/evidence/${e.id}/image`}
                    alt={`画面 ${stamp(e.data.time)}`}
                  />
                </a>
                <div>
                  <button onClick={() => seek(e.data.time)}>
                    {stamp(e.data.time)} · 画面文字
                  </button>
                  <p>{e.data.text}</p>
                  <small>
                    OCR 置信度 {Math.round(e.data.confidence * 100)}%
                  </small>
                </div>
              </div>
            ))}
          {history.length > 0 && (
            <details>
              <summary>
                <History size={12} />
                修改历史 · {history.length}
              </summary>
              {history.map((h) => (
                <div key={h.id}>
                  <small>
                    {new Date(h.created_at * 1000).toLocaleString("zh-CN")} ·{" "}
                    {h.data.reason === "ai" ? "AI 修订前" : "人工编辑前"}
                  </small>
                  <p>{h.data.text}</p>
                </div>
              ))}
            </details>
          )}
        </div>
      )}
    </article>
  );
}

export default function Transcript({
  snap,
  seek,
  refresh,
  error,
  tab,
  setTab,
  onGenerate,
}: {
  snap: Snapshot;
  seek: (n: number) => void;
  refresh: () => void;
  error: (s: string) => void;
  tab: string;
  setTab: (s: string) => void;
  onGenerate: (kind: string, template?: string, instruction?: string) => void;
}) {
  const [template, setTemplate] = useState("会议纪要"),
    [instruction, setInstruction] = useState(""),
    [source, setSource] = useState(false),
    [edit, setEdit] = useState(false),
    [draft, setDraft] = useState(""),
    [editVersion, setEditVersion] = useState(0),
    [selected, setSelected] = useState("");
  const area = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const extracts = snap.artifacts.filter((a) => a.data.kind === "extract");
  const current =
    extracts.find((a) => a.id === selected) || extracts[extracts.length - 1];
  const incomplete = snap.jobs.some(
    (j) => j.data.kind === "transcribe" && j.data.state !== "complete",
  );
  const busy = snap.jobs.some(
    (j) =>
      ["queued", "running"].includes(j.data.state) &&
      ["transcribe", "diarize", "revise", "extract"].includes(j.data.kind),
  );
  const charCount = snap.blocks.reduce(
    (n, b) =>
      n +
      (b.data.state === "accumulating"
        ? b.data.raw
        : (b.data.text ?? b.data.raw)
      ).length,
    0,
  );
  useEffect(() => {
    if (follow.current && !edit)
      area.current?.scrollTo({
        top: area.current.scrollHeight,
        behavior: "auto",
      });
  }, [
    snap.blocks.map((b) => `${b.id}:${b.version}`).join(","),
    current?.version,
    edit,
  ]);
  async function rename(s: Rec) {
    const name = await promptText("说话人名称", s.data.name);
    if (name)
      try {
        await api(`/speakers/${s.id}`, "PATCH", {
          version: s.version,
          data: { name },
        });
        refresh();
      } catch (e) {
        error((e as Error).message);
      }
  }
  async function save() {
    if (!current) return;
    try {
      await api(`/artifacts/${current.id}`, "PATCH", {
        version: editVersion,
        data: { text: draft },
      });
      setEdit(false);
      refresh();
    } catch (e) {
      error((e as Error).message);
    }
  }
  return (
    <>
      <div className="transcript-tabs">
        <div className="tabs">
          <button
            className={`${tab === "text" ? "active" : ""} ${!snap.blocks.length ? "muted-tab" : ""}`}
            onClick={() => setTab("text")}
          >
            <FileText size={15} />
            转录正文
            {snap.blocks.length > 0 && (
              <span className="count">{snap.blocks.length}</span>
            )}
          </button>
          <button
            className={`${tab === "extract" ? "active" : ""} ${!extracts.length ? "muted-tab" : ""}`}
            onClick={() => setTab("extract")}
          >
            <Sparkles size={15} />
            内容提取
          </button>
        </div>
        <select
          className="export-select"
          aria-label="导出文本"
          value=""
          onChange={(e) => {
            if (e.target.value)
              window.open(
                `/api/projects/${snap.project.id}/export?format=${e.target.value}${tab === "extract" && current ? "&artifact_id=" + current.id : ""}`,
              );
          }}
        >
          <option value="">↓ 导出</option>
          <option value="md">Markdown</option>
          <option value="txt">纯文本 TXT</option>
          {tab === "text" && <option value="srt">字幕 SRT</option>}
        </select>
      </div>
      {tab === "text" ? (
        <>
          <div className="transcript-subhead">
            <span>
              {charCount.toLocaleString()} 字 · {snap.blocks.length} 段
            </span>
            <div className="speakers-list">
              {snap.speakers.map((s) => (
                <button
                  key={s.id}
                  title="点击改名"
                  onClick={() => void rename(s)}
                >
                  {s.data.name}
                  <Pencil size={10} />
                </button>
              ))}
            </div>
          </div>
          <div
            className="transcript-scroll"
            ref={area}
            onScroll={() => {
              const el = area.current;
              follow.current =
                !!el && el.scrollHeight - el.scrollTop - el.clientHeight < 100;
            }}
          >
            {snap.blocks.length ? (
              snap.blocks.map((b) => (
                <Block
                  key={b.id}
                  b={b}
                  snap={snap}
                  seek={seek}
                  refresh={refresh}
                  error={error}
                />
              ))
            ) : (
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
                  上传音视频，点击「开始转录」。
                  <br />
                  识别出的内容将在这里逐段呈现。
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
                    可编辑正文
                  </span>
                </div>
              </div>
            )}
          </div>
          <div className="transcript-footer">
            <span>
              <span className="status-dot" />
              正文与原始识别结果均保存在本地
            </span>
            <button
              onClick={() => {
                follow.current = true;
                area.current?.scrollTo({
                  top: area.current.scrollHeight,
                  behavior: "smooth",
                });
              }}
            >
              回到最新 ↓
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="extract-controls">
            <div className="extract-first-row">
              <select
                aria-label="提取模板"
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
              >
                {[
                  "会议纪要",
                  "内容摘要",
                  "面试记录",
                  "行动清单",
                  "自定义要求",
                ].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              <button
                className="button primary"
                disabled={busy || incomplete || !snap.blocks.length}
                onClick={() => {
                  setSelected("");
                  setEdit(false);
                  onGenerate("extract", template, instruction);
                }}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={14} />
                ) : (
                  <Sparkles size={14} />
                )}
                生成内容提取
              </button>
            </div>
            <textarea
              rows={2}
              placeholder="补充整理要求（可选），例如：重点保留结论与下一步行动"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
            {(busy || incomplete) && (
              <small>
                等待转写、说话人分析、修订或当前生成任务完成后，可开始新的提取。
              </small>
            )}
          </div>
          {current && (
            <div className="extract-toolbar">
              <select
                aria-label="结果版本"
                value={current.id}
                onChange={(e) => {
                  setSelected(e.target.value);
                  setEdit(false);
                }}
              >
                {extracts.map((a, i) => (
                  <option key={a.id} value={a.id}>
                    结果 {i + 1} ·{" "}
                    {new Date(a.created_at * 1000).toLocaleTimeString("zh-CN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {a.data.state === "generating" ? " · 生成中" : ""}
                  </option>
                ))}
              </select>
              <span className="grow" />
              <button onClick={() => setSource(!source)}>
                {source ? "预览" : "Markdown"}
              </button>
              {current.data.state === "complete" && !edit && (
                <button
                  onClick={() => {
                    setDraft(current.data.text);
                    setEditVersion(current.version);
                    setEdit(true);
                  }}
                >
                  <Pencil size={12} />
                  编辑
                </button>
              )}
            </div>
          )}
          {current &&
            current.data.source_revision !==
              snap.project.data.transcript_revision && (
              <div className="notice">
                源文已更新，可重新生成；当前结果仍然保留。
              </div>
            )}
          <div
            className="extract-scroll"
            ref={area}
            onScroll={() => {
              const el = area.current;
              follow.current =
                !!el && el.scrollHeight - el.scrollTop - el.clientHeight < 100;
            }}
          >
            {current ? (
              edit ? (
                <>
                  <textarea
                    className="markdown-edit"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                  <div className="inline-actions">
                    <button onClick={() => setEdit(false)}>取消</button>
                    <button
                      className="primary-small"
                      onClick={async () => {
                        if (current.version !== editVersion) {
                          if (
                            await confirmAction(
                              "结果已在另一页面更新。服务器内容：\n" +
                                current.data.text +
                                "\n\n保留你的草稿并基于最新版本继续编辑？",
                            )
                          )
                            setEditVersion(current.version);
                        } else void save();
                      }}
                    >
                      {current.version !== editVersion
                        ? "核对最新版本"
                        : "保存修改"}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  {source ? (
                    <pre className="markdown-source">{current.data.text}</pre>
                  ) : (
                    <div className="markdown">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        skipHtml
                        components={{
                          img: ({ alt }) => (
                            <span>[图片：{alt || "未载入"}]</span>
                          ),
                        }}
                      >
                        {current.data.text}
                      </ReactMarkdown>
                    </div>
                  )}
                  {current.data.state === "generating" && (
                    <div className="generating">
                      <LoaderCircle size={14} className="spin" />
                      正在生成，内容会持续更新
                    </div>
                  )}
                  {current.data.state === "failed" && (
                    <div className="notice">
                      生成中断，已保留输出，可在任务区重试。
                    </div>
                  )}
                  {current.data.notice && (
                    <div className="notice">{current.data.notice}</div>
                  )}
                </>
              )
            ) : (
              <div className="empty-small">
                <Sparkles size={30} />
                <h3>从原话，提炼出重点</h3>
                <p>
                  选择整理方式，生成摘要、纪要或行动清单。
                  <br />
                  文字完成后将自动生成内容图谱。
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}
