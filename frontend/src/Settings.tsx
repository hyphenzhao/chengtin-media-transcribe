import { confirmAction } from "./Dialogs";
import { useState } from "react";
import {
  Check,
  KeyRound,
  LoaderCircle,
  Plus,
  Server,
  Trash2,
  X,
} from "lucide-react";
import { api, type Rec } from "./api";
export default function Settings({
  providers,
  close,
  refresh,
  error,
}: {
  providers: Rec[];
  close: () => void;
  refresh: () => Promise<void>;
  error: (s: string) => void;
}) {
  const [selected, setSelected] = useState<Rec | null>(null),
    [name, setName] = useState(""),
    [url, setUrl] = useState(""),
    [model, setModel] = useState(""),
    [key, setKey] = useState(""),
    [busy, setBusy] = useState(false),
    [test, setTest] = useState("");
  function choose(p: Rec | null) {
    setSelected(p);
    setName(p?.data.name || "");
    setUrl(p?.data.base_url || "");
    setModel(p?.data.model || "");
    setKey("");
    setTest("");
  }
  async function save() {
    setBusy(true);
    try {
      await api(
        selected ? `/providers/${selected.id}` : "/providers",
        selected ? "PUT" : "POST",
        {
          name,
          base_url: url,
          model,
          api_key: key,
          version: selected?.version,
        },
      );
      await refresh();
      choose(null);
      setTest("接口配置已保存，请在项目中选择。");
    } catch (e) {
      error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function testConnection(p: Rec) {
    setBusy(true);
    setTest("");
    try {
      const r = await api(`/providers/${p.id}/test`, "POST");
      setTest(r.message || "连接成功");
    } catch (e) {
      setTest((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(p: Rec) {
    if (!(await confirmAction(`删除接口「${p.data.name}」？`))) return;
    try {
      await api(`/providers/${p.id}`, "DELETE");
      await refresh();
      choose(null);
    } catch (e) {
      error((e as Error).message);
    }
  }
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label="AI 接口设置"
      >
        <div className="modal-heading">
          <div className="settings-icon">
            <KeyRound size={21} />
          </div>
          <div>
            <h2>AI 接口设置</h2>
            <p>连接你选择的模型，为文字补上理解。</p>
          </div>
          <button
            className="icon-button close"
            onClick={close}
            aria-label="关闭设置"
          >
            <X size={20} />
          </button>
        </div>
        <div className="settings-body">
          <aside>
            {providers.map((p) => (
              <div
                className={`provider-row ${selected?.id === p.id ? "selected" : ""}`}
                key={p.id}
              >
                <button onClick={() => choose(p)}>
                  <Server size={16} />
                  <span>
                    <strong>{p.data.name}</strong>
                    <small>{p.data.model}</small>
                  </span>
                </button>
                <button title="删除接口" onClick={() => void remove(p)}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button className="new-provider" onClick={() => choose(null)}>
              <Plus size={15} />
              添加接口
            </button>
          </aside>
          <div className="provider-form">
            <h3>{selected ? "编辑接口" : "新建兼容接口"}</h3>
            <label>
              配置名称
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：我的文本助手"
              />
            </label>
            <label>
              Base URL
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://你的服务地址/v1"
              />
              <small>填写兼容 Chat Completions 的基础地址。</small>
            </label>
            <label>
              模型名称
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="服务商提供的模型 ID"
              />
            </label>
            <label>
              API Key
              <input
                type="password"
                autoComplete="new-password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  selected?.data.has_key
                    ? "已保存；留空则保留原密钥"
                    : "密钥仅存储在服务器"
                }
              />
            </label>
            <div className="form-actions">
              {selected && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void testConnection(selected)}
                >
                  测试已保存配置
                </button>
              )}
              <button
                className="button primary"
                disabled={busy || !name || !url || !model}
                onClick={() => void save()}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={14} />
                ) : (
                  <Check size={14} />
                )}
                保存配置
              </button>
            </div>
            {test && <div className="connection-result">{test}</div>}
          </div>
        </div>
        <footer>
          共享工作区 ·
          配置对内网访问者可用，密钥不会返回浏览器。只有转录文字和视频 OCR
          文字会发送至所选接口。
        </footer>
      </section>
    </div>
  );
}
