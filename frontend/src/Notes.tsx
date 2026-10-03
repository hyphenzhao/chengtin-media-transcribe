import { confirmAction } from "./Dialogs";
import { useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import { Node, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";
import {
  Bold,
  Italic,
  List,
  ImagePlus,
  Paperclip,
  LoaderCircle,
} from "lucide-react";
import { api, type Rec, uploadFile, size } from "./api";

const localMedia = (el: HTMLElement) =>
  /^\/api\/assets\/[a-f0-9]{32}\/media$/.test(el.getAttribute("src") || "")
    ? {}
    : false;
const LocalImage = Image.extend({
  parseHTML() {
    return [{ tag: "img[src]", getAttrs: localMedia }];
  },
});
const Media = Node.create({
  name: "media",
  group: "block",
  atom: true,
  addAttributes() {
    return { src: { default: "" }, type: { default: "audio" } };
  },
  parseHTML() {
    return [
      { tag: "video", getAttrs: localMedia },
      { tag: "audio", getAttrs: localMedia },
    ];
  },
  renderHTML({ HTMLAttributes }) {
    const tag = HTMLAttributes.type === "video" ? "video" : "audio";
    return [
      tag,
      mergeAttributes(HTMLAttributes, { controls: "", preload: "metadata" }),
    ];
  },
});

export default function Notes({
  pid,
  value,
  version,
  assets,
  refresh,
  error,
}: {
  pid: string;
  value: any;
  version: number;
  assets: Rec[];
  refresh: () => void;
  error: (s: string) => void;
}) {
  const [saving, setSaving] = useState("已保存");
  const [conflict, setConflict] = useState(false);
  const [upload, setUpload] = useState<number | null>(null);
  const cacheKey = "chengtin-note-" + pid;
  const cached = useRef<any>(
    (() => {
      try {
        return JSON.parse(localStorage.getItem(cacheKey) || "null");
      } catch {
        return null;
      }
    })(),
  );
  const revision = useRef(cached.current?.version ?? version);
  const pending = useRef<any>(cached.current?.value || null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const file = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  async function flush() {
    if (conflict || inFlight.current || !pending.current) return;
    inFlight.current = true;
    const data = pending.current;
    pending.current = null;
    if (mounted.current) setSaving("保存中");
    try {
      const result = await api(`/projects/${pid}/notes`, "PATCH", {
        version: revision.current,
        data: { notes: data },
      });
      revision.current = result.version;
      if (pending.current) {
        localStorage.setItem(
          cacheKey,
          JSON.stringify({ version: revision.current, value: pending.current }),
        );
      } else localStorage.removeItem(cacheKey);
      if (mounted.current) setSaving("已保存");
      refresh();
    } catch (e) {
      pending.current = pending.current || data;
      if (mounted.current) {
        setSaving("草稿已保留");
        setConflict(true);
      }
      error((e as Error).message);
      inFlight.current = false;
      return;
    }
    inFlight.current = false;
    if (pending.current) void flush();
  }
  const editor = useEditor({
    extensions: [
      StarterKit,
      LocalImage,
      Media,
      Placeholder.configure({ placeholder: "记下重要细节，或粘贴图片…" }),
    ],
    content: cached.current?.value || value || undefined,
    editorProps: {
      attributes: { class: "notes-editor" },
      handlePaste: (_, e) => {
        const image = Array.from(e.clipboardData?.files || []).find((f) =>
          f.type.startsWith("image/"),
        );
        if (image) {
          e.preventDefault();
          void attach(image);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor }) => {
      setSaving("未保存");
      if (timer.current) clearTimeout(timer.current);
      const json = editor.getJSON();
      pending.current = json;
      localStorage.setItem(
        cacheKey,
        JSON.stringify({ version: revision.current, value: json }),
      );
      timer.current = setTimeout(() => void flush(), 800);
    },
    onBlur: () => void flush(),
  });
  useEffect(() => {
    if (cached.current) setSaving("已恢复本地草稿");
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
      void flush();
    };
  }, []);
  useEffect(() => {
    if (
      version > revision.current &&
      !pending.current &&
      !inFlight.current &&
      !editor?.isFocused
    ) {
      editor?.commands.setContent(
        value || { type: "doc", content: [{ type: "paragraph" }] },
        { emitUpdate: false },
      );
      revision.current = version;
    }
  }, [version, value, editor]);
  async function reconcile() {
    try {
      const latest = await api(`/projects/${pid}`);
      const current = JSON.stringify(latest.project.data.notes);
      if (
        await confirmAction(
          "另一页面已更新笔记或保存失败。你的草稿已保留在浏览器。确认用当前草稿替换服务器笔记？\n服务器内容：" +
            current.slice(0, 700),
        )
      ) {
        revision.current = latest.project.data.notes_version || 0;
        setConflict(false);
        const data = pending.current || editor?.getJSON();
        await api(`/projects/${pid}/notes`, "PATCH", {
          version: revision.current,
          data: { notes: data },
        });
        revision.current++;
        pending.current = null;
        localStorage.removeItem(cacheKey);
        setSaving("已保存");
        refresh();
      }
    } catch (e) {
      error((e as Error).message);
    }
  }
  async function attach(f: File) {
    try {
      setUpload(0);
      const a = await uploadFile(pid, f, "supplement", setUpload);
      const src = `/api/assets/${a.id}/media`;
      if (a.data.type === "image")
        editor?.chain().focus().setImage({ src, alt: a.data.name }).run();
      else
        editor
          ?.chain()
          .focus()
          .insertContent({ type: "media", attrs: { src, type: a.data.type } })
          .run();
      refresh();
    } catch (e) {
      error((e as Error).message);
    } finally {
      setUpload(null);
    }
  }
  return (
    <>
      <div className="editor-toolbar">
        <button
          className={editor?.isActive("bold") ? "selected" : ""}
          onClick={() => editor?.chain().focus().toggleBold().run()}
          title="加粗"
        >
          <Bold size={15} />
        </button>
        <button
          onClick={() => editor?.chain().focus().toggleItalic().run()}
          title="斜体"
        >
          <Italic size={15} />
        </button>
        <button
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
          title="项目列表"
        >
          <List size={16} />
        </button>
        <span className="tool-divider" />
        <button onClick={() => file.current?.click()} title="插入图片或音视频">
          <ImagePlus size={16} />
        </button>
        <button onClick={() => file.current?.click()} title="添加附件">
          <Paperclip size={16} />
        </button>
        <span className="toolbar-status">
          {upload !== null ? (
            <>
              <LoaderCircle size={12} className="spin" />
              上传 {upload}%
            </>
          ) : (
            saving
          )}
        </span>
        <input
          ref={file}
          type="file"
          hidden
          accept="image/png,image/jpeg,image/webp,image/gif,audio/*,video/*"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void attach(f);
            e.target.value = "";
          }}
        />
      </div>
      {conflict && (
        <div className="notice">
          草稿已保留。
          <button onClick={() => void reconcile()}>核对并重新保存</button>
        </div>
      )}
      <div className="note-scroll">
        <EditorContent editor={editor} />
        {assets.length > 0 && (
          <div className="attachment-list">
            {assets.map((a) => (
              <a
                key={a.id}
                href={`/api/assets/${a.id}/media`}
                target="_blank"
                rel="noreferrer"
              >
                <Paperclip size={12} />
                <span>{a.data.name}</span>
                <small>{size(a.data.size)}</small>
              </a>
            ))}
          </div>
        )}
      </div>
      <div className="note-foot">仅在本地保存 · 不参与 AI 处理</div>
    </>
  );
}
