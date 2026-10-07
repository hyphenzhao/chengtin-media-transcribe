import { useEffect, useRef, useState } from "react";
import {
  Download,
  Maximize2,
  Minus,
  Plus,
  RotateCcw,
  GitBranch,
  LoaderCircle,
} from "lucide-react";
import mermaid from "mermaid";
import { type Rec } from "./api";
mermaid.initialize({
  startOnLoad: false,
  htmlLabels: false,
  securityLevel: "strict",
  theme: "base",
  fontFamily: "system-ui, sans-serif",
  themeVariables: {
    primaryColor: "#eaf2ff",
    primaryBorderColor: "#87b4ff",
    primaryTextColor: "#203551",
    lineColor: "#7791b5",
    background: "#ffffff",
    ...Object.fromEntries(
      Array.from({ length: 12 }, (_, i) => [
        "cScale" + i,
        ["#dbeafe", "#e0e7ff", "#bfdbfe", "#e0f2fe"][i % 4],
      ]),
    ),
    ...Object.fromEntries(
      Array.from({ length: 12 }, (_, i) => ["cScaleLabel" + i, "#1e3a5f"]),
    ),
  },
  flowchart: { htmlLabels: false, useMaxWidth: false },
  mindmap: { useMaxWidth: false },
});
let serial = 0;
export default function Diagram({
  item,
  onGenerate,
  busy,
  stale,
  jobError,
  disabledReason,
}: {
  item?: Rec;
  onGenerate: (instruction: string, type: string) => void;
  busy: boolean;
  stale: boolean;
  jobError?: string;
  disabledReason?: string;
}) {
  const [download, setDownload] = useState<{
    url: string;
    name: string;
  } | null>(null);
  const [svg, setSvg] = useState(""),
    [renderError, setRenderError] = useState(""),
    [zoom, setZoom] = useState(1),
    [instruction, setInstruction] = useState(""),
    [type, setType] = useState("auto");
  const area = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState({ width: 1200, height: 800 });
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    const canvas = area.current;
    if (!canvas || !svg) return;
    const fit = () => {
      if (canvas.clientWidth < 40 || canvas.clientHeight < 40) return;
      setZoom(
        Math.max(
          0.02,
          Math.min(
            1,
            (canvas.clientWidth - 36) / bounds.width,
            (canvas.clientHeight - 36) / bounds.height,
          ),
        ),
      );
    };
    const observer = new ResizeObserver(fit);
    observer.observe(canvas);
    fit();
    return () => observer.disconnect();
  }, [svg, bounds.width, bounds.height]);
  useEffect(() => {
    if (!fullscreen) return;
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [fullscreen]);
  useEffect(() => {
    let cancelled = false;
    if (!item?.data.mermaid) {
      setSvg("");
      return;
    }
    setRenderError("");
    mermaid
      .render("graph" + ++serial, item.data.mermaid)
      .then(({ svg }) => {
        if (!cancelled) {
          setSvg(svg);
          const box = new DOMParser()
            .parseFromString(svg, "image/svg+xml")
            .documentElement.getAttribute("viewBox")
            ?.split(/\s+/)
            .map(Number);
          if (box) setBounds({ width: box[2], height: box[3] });
        }
      })
      .catch(() => {
        if (!cancelled) setRenderError("图表排版未成功，请重新生成。");
      });
    return () => {
      cancelled = true;
    };
  }, [item?.data.mermaid]);
  async function saveDiagram(content: BlobPart, name: string, type: string) {
    if (!item) return;
    setRenderError("");
    try {
      const format = type === "image/png" ? "png" : "svg";
      const res = await fetch(
        `/api/artifacts/${item.id}/rendered?version=${item.version}&format=${format}`,
        {
          method: "POST",
          headers: { "Content-Type": type },
          body: new Blob([content], { type }),
        },
      );
      if (!res.ok) throw new Error((await res.json()).detail || "导出失败");
      const { url } = await res.json();
      setDownload({ url, name });
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      setRenderError((e as Error).message);
    }
  }

  async function png() {
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    const el = doc.documentElement;
    const box = el.getAttribute("viewBox")?.split(/\s+/).map(Number) || [
      0, 0, 1200, 800,
    ];
    const width = Math.min(4096, Math.max(600, box[2] * 2)),
      height = (width * box[3]) / box[2];
    el.setAttribute("width", String(width));
    el.setAttribute("height", String(height));
    const blob = new Blob([new XMLSerializer().serializeToString(el)], {
      type: "image/svg+xml",
    });
    const url = URL.createObjectURL(blob);
    const img = new window.Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(img, 0, 0, width, height);
      canvas.toBlob((b) => {
        if (b) saveDiagram(b, "内容图谱.png", "image/png");
      });
      URL.revokeObjectURL(url);
    };
    img.src = url;
  }
  return (
    <div
      className={`diagram-panel ${fullscreen ? "diagram-fullscreen" : ""}`}
      role={fullscreen ? "dialog" : undefined}
      aria-label={fullscreen ? "内容图谱全屏" : undefined}
    >
      {fullscreen && (
        <button className="diagram-close" onClick={() => setFullscreen(false)}>
          退出全屏 ✕
        </button>
      )}
      <div className="diagram-intro">
        <span className="eyebrow">从内容到结构</span>
        <h2>让思路清晰可见</h2>
        <p>内容提取完成后，自动整理为流程图或思维图。</p>
      </div>
      {jobError && (
        <div className="notice">
          图谱未完成：{jobError}。已有结果仍保留，可重新生成。
        </div>
      )}
      {busy && svg && (
        <div className="notice">正在生成新图谱，先显示上一版。</div>
      )}
      {stale && (
        <div className="notice">源文已更新，当前图谱保留上次生成的内容。</div>
      )}
      <div className="diagram-canvas" ref={area}>
        {svg ? (
          <div
            className="svg-stage"
            style={{ width: bounds.width * zoom, height: bounds.height * zoom }}
          >
            <div
              className="svg-content"
              style={{
                width: bounds.width,
                height: bounds.height,
                transform: `scale(${zoom})`,
              }}
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          </div>
        ) : (
          <div className="empty-small">
            {busy ? (
              <LoaderCircle className="spin" size={32} />
            ) : (
              <GitBranch size={34} />
            )}
            <strong>{busy ? "正在整理内容结构" : "等待生成内容图谱"}</strong>
            <p>{renderError || "先在「内容提取」中生成一份摘要或纪要。"}</p>
          </div>
        )}
      </div>
      {svg && (
        <div className="diagram-tools">
          <button
            onClick={() => setZoom(Math.max(0.02, zoom / 1.3))}
            title="缩小"
          >
            <Minus size={16} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(Math.min(3, zoom * 1.3))} title="放大">
            <Plus size={16} />
          </button>
          <button onClick={() => setFullscreen(true)} title="全屏">
            <Maximize2 size={16} />
          </button>
          <span className="grow" />
          <button
            onClick={() => saveDiagram(svg, "内容图谱.svg", "image/svg+xml")}
          >
            <Download size={14} />
            SVG
          </button>
          <button onClick={() => void png()}>PNG</button>
        </div>
      )}
      {renderError && <div className="notice">{renderError}</div>}
      {download && (
        <div className="notice">
          文件已生成：
          <a href={download.url} download={download.name}>
            {download.name} · 下载
          </a>
        </div>
      )}
      {disabledReason && <div className="notice">{disabledReason}</div>}
      <div className="diagram-form">
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="auto">自动选择图表类型</option>
          <option value="flowchart">流程图</option>
          <option value="mindmap">思维图</option>
        </select>
        <textarea
          rows={3}
          placeholder="重新生成的要求，例如：按三个主要议题归纳，保留行动项"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
        />
        <button
          className="button secondary"
          disabled={busy || !!disabledReason}
          onClick={() => onGenerate(instruction, type)}
        >
          <RotateCcw size={15} />
          {svg ? "重新生成图谱" : "生成图谱"}
        </button>
      </div>
    </div>
  );
}
