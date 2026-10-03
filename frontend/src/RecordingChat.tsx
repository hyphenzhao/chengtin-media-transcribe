import { useEffect, useRef, useState } from "react";
import {
  Clock3,
  LoaderCircle,
  MessageCircle,
  Send,
  Square,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Rec, type Snapshot } from "./api";

export default function RecordingChat({
  snap,
  seek,
  refresh,
  error,
  onExtract,
  onConfigure,
}: {
  snap: Snapshot;
  seek: (time: number) => void;
  refresh: () => void;
  error: (message: string) => void;
  onExtract: () => void;
  onConfigure: () => void;
}) {
  const pid = snap.project.id;
  const cacheKey = "chengtin-question-" + pid;
  const [question, setQuestion] = useState(
    localStorage.getItem(cacheKey) || "",
  );
  const [sending, setSending] = useState(false);
  const request = useRef({ question: "", id: "" });
  const submitting = useRef(false);
  const area = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const turns = snap.chats || [];
  const extract = snap.artifacts
    .filter((a) => a.data.kind === "extract" && a.data.state === "complete")
    .at(-1);
  const active = snap.jobs.find(
    (j) =>
      j.data.kind === "chat" && ["queued", "running"].includes(j.data.state),
  );
  const processing = snap.jobs.some(
    (j) =>
      ["transcribe", "revise", "diarize"].includes(j.data.kind) &&
      ["queued", "running"].includes(j.data.state),
  );
  const provider = snap.project.data.settings.provider_id;
  const ready = Boolean(extract && provider && !processing);
  useEffect(() => {
    if (question) localStorage.setItem(cacheKey, question);
    else localStorage.removeItem(cacheKey);
  }, [cacheKey, question]);
  useEffect(() => {
    if (follow.current)
      area.current?.scrollTo({ top: area.current.scrollHeight });
  }, [turns.map((t) => `${t.id}:${t.version}`).join(",")]);
  async function send() {
    const text = question.trim();
    if (!text || !ready || active || submitting.current) return;
    submitting.current = true;
    setSending(true);
    if (request.current.question !== text)
      request.current = {
        question: text,
        // crypto.randomUUID is unavailable on an HTTP LAN origin.
        id: Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
          b.toString(16).padStart(2, "0"),
        ).join(""),
      };
    try {
      await api(`/projects/${pid}/chat`, "POST", {
        question: text,
        request_id: request.current.id,
      });
      setQuestion("");
      request.current = { question: "", id: "" };
      follow.current = true;
      refresh();
    } catch (e) {
      error((e as Error).message);
    } finally {
      submitting.current = false;
      setSending(false);
    }
  }
  async function task(job: Rec, action: string) {
    try {
      await api(`/jobs/${job.id}/${action}`, "POST");
      refresh();
    } catch (e) {
      error((e as Error).message);
    }
  }
  return (
    <div className="recording-chat">
      <div
        className="chat-scroll"
        ref={area}
        onScroll={() => {
          const el = area.current;
          follow.current =
            !!el && el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        aria-label="录音问答记录"
        aria-busy={Boolean(active)}
      >
        {!extract ? (
          <div className="chat-empty">
            <MessageCircle size={23} />
            <div>
              <strong>提取之后，继续追问</strong>
              <p>先生成内容提取，就可以询问这段录音的细节、结论或待办。</p>
            </div>
            <button className="primary-small" onClick={onExtract}>
              前往内容提取
            </button>
          </div>
        ) : !turns.length ? (
          <div className="chat-empty">
            <Sparkles size={22} />
            <div>
              <strong>围绕这段录音提问</strong>
              <p>例如：有哪些明确的结论？哪些问题还没有回答？</p>
            </div>
          </div>
        ) : (
          turns.map((turn) => {
            const d = turn.data,
              job = snap.jobs.find((j) => j.id === d.job_id);
            const busy = job && ["queued", "running"].includes(job.data.state);
            const failed =
              job && ["failed", "cancelled"].includes(job.data.state);
            return (
              <article className="chat-turn" key={turn.id}>
                <div className="chat-question">
                  <span>提问</span>
                  <p>{d.question}</p>
                </div>
                <div className="chat-answer">
                  <div className="chat-answer-label">
                    <Sparkles size={12} />
                    录音助手
                    {busy && (
                      <span>
                        <LoaderCircle size={12} className="spin" />
                        {job.data.stage}
                      </span>
                    )}
                  </div>
                  {d.text ? (
                    <div className="markdown">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        skipHtml
                        components={{
                          img: ({ alt }) => (
                            <span>[图片：{alt || "未载入"}]</span>
                          ),
                          a: ({ children }) => <span>{children}</span>,
                        }}
                      >
                        {d.text}
                      </ReactMarkdown>
                    </div>
                  ) : !busy && !failed ? (
                    <p>等待回答…</p>
                  ) : null}
                  {!busy && d.citations?.length > 0 && (
                    <div className="chat-citations">
                      <span>回听原文</span>
                      {d.citations.map((c: { label: string; time: number }) => (
                        <button key={c.label} onClick={() => seek(c.time)}>
                          <Clock3 size={11} />
                          {c.label}
                        </button>
                      ))}
                    </div>
                  )}
                  {d.source_revision !==
                    snap.project.data.transcript_revision && (
                    <small className="chat-stale">
                      正文已更新；这条回答基于提问时的版本。
                    </small>
                  )}
                  {failed && (
                    <div className="chat-failure">
                      <span>
                        {job.data.state === "cancelled"
                          ? "已停止，已生成内容已保留"
                          : job.data.error || "回答中断，可重试"}
                      </span>
                      <button
                        disabled={Boolean(active)}
                        onClick={() => void task(job, "retry")}
                      >
                        <RotateCcw size={12} />
                        重试回答
                      </button>
                    </div>
                  )}
                </div>
              </article>
            );
          })
        )}
      </div>
      {extract && (
        <>
          {!provider && (
            <div className="chat-notice">
              选择项目的 AI 接口后即可提问。
              <button onClick={onConfigure}>配置接口</button>
            </div>
          )}
          {processing && (
            <div className="chat-notice">
              正文正在处理，完成后即可继续提问。
            </div>
          )}
          {extract.data.source_revision !==
            snap.project.data.transcript_revision && (
            <div className="chat-notice">
              提取结果已过期，问答将以最新正文为准。
            </div>
          )}
          <form
            className="chat-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              aria-label="询问录音内容"
              placeholder="询问这段录音…"
              rows={1}
              maxLength={2000}
              value={question}
              disabled={!ready || Boolean(active) || sending}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            {active ? (
              <button
                type="button"
                className="chat-send"
                title="停止回答"
                aria-label="停止回答"
                disabled={active.data.cancel}
                onClick={() => void task(active, "cancel")}
              >
                <Square size={14} />
              </button>
            ) : (
              <button
                type="submit"
                className="chat-send"
                title="发送问题"
                aria-label="发送问题"
                disabled={!ready || !question.trim() || sending}
              >
                {sending ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <Send size={15} />
                )}
              </button>
            )}
          </form>
          <div className="chat-footnote">
            仅依据正文与提取结果 · 笔记和附件不参与 · Enter 发送
          </div>
        </>
      )}
    </div>
  );
}
