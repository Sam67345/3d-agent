// M13 React 前端主界面：完全复刻 Gradio 版的三段式布局
//   左栏：➕新会话 + 历史会话竖排列表 + LLM设置/切片信息折叠区
//   中栏：聊天区（气泡、markdown、确认/取消/跳过确认）
//   右栏：3D 视图侧边栏，默认收起，展开后是历史图下拉 + iframe
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  figureSrc,
  type ChatReply,
  type FigureItem,
  type PanelData,
  type SessionItem,
  type Step,
} from "./api";
import Markdown from "./components/Markdown";

type Msg = { role: "user" | "assistant"; content: string };

export default function App() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [skipConfirm, setSkipConfirm] = useState(false);
  const [pending, setPending] = useState<Step[] | null>(null);

  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [panel, setPanel] = useState<PanelData | null>(null);
  const [header, setHeader] = useState("");

  const [showView, setShowView] = useState(false);
  const [figures, setFigures] = useState<FigureItem[]>([]);
  const [figLabel, setFigLabel] = useState("");
  const [current, setCurrent] = useState<string>("");
  const [uploading, setUploading] = useState(false);
  const [figMsg, setFigMsg] = useState("");
  const [showPathInput, setShowPathInput] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollDown = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(scrollDown, [msgs, pending]);

  const refreshSide = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.sessions(), api.panel()]);
      setSessions(s.sessions);
      setPanel(p);
    } catch {
      /* 后端未起时静默 */
    }
  }, []);

  const refreshFigures = useCallback(async () => {
    try {
      const f = await api.figures();
      setFigures(f.figures);
      setFigLabel(f.label);
      setCurrent((c) => (c && f.figures.some((x) => x.path === c) ? c : f.figures[0]?.path || ""));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refreshSide();
    api.health().then((h) => {
      if (h.agent) refreshFigures();
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const push = (role: Msg["role"], content: string) =>
    setMsgs((m) => [...m, { role, content }]);

  /** 发指令：后端同步等长任务 */
  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    push("user", text);
    setBusy(true);
    try {
      const r: ChatReply = await api.chat(text, skipConfirm);
      if (r.status === "confirm" && r.steps?.length) {
        push("assistant", r.plan_card || r.reply);
        setPending(r.steps);
      } else {
        push("assistant", r.reply);
        setPending(null);
      }
      await Promise.all([refreshSide(), refreshFigures()]);
    } catch (e) {
      push("assistant", `❌ 请求失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const doConfirm = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const r = await api.confirm(pending);
      push("assistant", r.reply);
      setPending(null);
      await Promise.all([refreshSide(), refreshFigures()]);
    } catch (e) {
      push("assistant", `❌ ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const doCancel = async () => {
    if (!pending) return;
    try {
      await api.cancel();
    } catch {
      /* ignore */
    }
    push("assistant", "↩️ 已取消，等待下一条指令。");
    setPending(null);
  };

  const newSession = async () => {
    setBusy(true);
    try {
      await api.newSession();
      setMsgs([]);
      setPending(null);
      await refreshSide();
    } finally {
      setBusy(false);
    }
  };

  const restore = async (p: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.restoreSession(p);
      push("assistant", r.status === "ok" ? `✅ ${r.reply}` : `❌ 恢复失败：${r.reply}`);
      await refreshSide();
    } catch (e) {
      push("assistant", `❌ ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const toggleView = async () => {
    const next = !showView;
    setShowView(next);
    if (next && figures.length === 0) await refreshFigures();
  };

  /** 弹窗选中的本地 html → base64 上传（浏览器拿不到本地路径，只能传内容） */
  const onPickFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";                 // 允许重复选同一个文件
    if (!files.length) return;
    setUploading(true);
    setFigMsg(`正在导入 ${files.length} 个文件…`);
    try {
      const payload = await Promise.all(
        files.map(async (f) => {
          const buf = await f.arrayBuffer();
          let bin = "";
          const bytes = new Uint8Array(buf);
          for (let i = 0; i < bytes.length; i += 0x8000) {
            bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
          }
          return { name: f.name, content_b64: btoa(bin) };
        }),
      );
      const r = await api.uploadFigures(payload);
      setFigures(r.figures);
      setFigLabel(r.label);
      if (r.saved.length) {
        setCurrent(r.saved[0]);
        setFigMsg(`✅ 已导入 ${r.saved.length} 个：${r.saved.join("、")}`);
      } else {
        setFigMsg(`⚠ 未导入：${r.skipped.join("；") || "未知原因"}`);
      }
    } catch (err) {
      setFigMsg(`❌ 导入失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      {/* 顶部状态条 */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-2">
        <div className="text-[13px] text-slate-600">
          SpatialZ 3D 组织重构智能体 · React 前端
        </div>
        <div className="text-[12px] text-slate-500">{header || " "}</div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ---------------- 左栏 ---------------- */}
        <aside className="flex w-64 shrink-0 flex-col gap-3 overflow-y-auto border-r border-slate-200 bg-white p-3">
          <button
            onClick={newSession}
            disabled={busy}
            className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-[13px] font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
          >
            ＋ 新会话
          </button>

          <div className="flex min-h-0 flex-1 flex-col">
            <div className="mb-1 text-[12px] font-medium text-slate-500">
              📂 历史会话（{sessions.length}）
            </div>
            <div className="max-h-56 overflow-y-auto pr-1">
              {sessions.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-[12px] text-slate-400">
                  暂无归档会话
                </div>
              )}
              {sessions.map((s) => (
                <button
                  key={s.path}
                  onClick={() => restore(s.path)}
                  disabled={busy}
                  className="mb-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left transition hover:border-indigo-300 hover:bg-indigo-50/40 disabled:opacity-50"
                >
                  <div className="truncate text-[12.5px] font-medium text-slate-800">
                    {s.title}
                  </div>
                  <div className="mt-0.5 flex justify-between text-[11px] text-slate-400">
                    <span>{s.saved_at}</span>
                    <span>
                      {s.n_slices} 切片 / {s.n_virtual} 虚拟
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* LLM 设置 */}
          <details className="rounded-lg border border-slate-200">
            <summary className="cursor-pointer px-3 py-2 text-[12.5px] font-medium text-slate-700">
              ⚙️ LLM 设置
            </summary>
            <LlmBox onSaved={(h) => setHeader(h)} />
          </details>

          {/* 切片信息 */}
          <details className="rounded-lg border border-slate-200">
            <summary className="cursor-pointer px-3 py-2 text-[12.5px] font-medium text-slate-700">
              📊 切片信息
            </summary>
            <div className="px-3 pb-3">
              {panel && panel.slices.length > 0 ? (
                <table className="w-full text-[11.5px]">
                  <thead>
                    <tr className="text-left text-slate-400">
                      <th className="py-1 font-normal">名称</th>
                      <th className="py-1 font-normal">spots</th>
                      <th className="py-1 font-normal">基因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {panel.slices.map((s) => (
                      <tr key={s.sample_id} className="border-t border-slate-100">
                        <td className="py-1">{s.sample_id}</td>
                        <td className="py-1 text-slate-600">{s.n_spots}</td>
                        <td className="py-1 text-slate-600">{s.n_genes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="text-[11.5px] text-slate-400">未加载切片</div>
              )}
              <div className="mt-2 text-[11.5px] text-slate-500">
                虚拟切片：{panel?.n_virtual ?? 0} 层
              </div>
              <div className="mt-1 break-all text-[11px] text-slate-400">
                {panel?.run_dir}
              </div>
            </div>
          </details>
        </aside>

        {/* ---------------- 中栏：对话 ---------------- */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
            <button
              onClick={toggleView}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-700 transition hover:border-indigo-300 hover:text-indigo-600"
            >
              🗺️ 3D 视图
            </button>
            <span className="text-[12px] text-slate-400">
              {busy ? "执行中…" : "就绪"}
            </span>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {msgs.length === 0 && (
              <div className="mt-16 text-center text-[13px] text-slate-400">
                用中文描述你想做的事，例如「加载 data 下的 151673 切片」
              </div>
            )}
            <div className="mx-auto max-w-3xl space-y-4">
              {msgs.map((m, i) => (
                <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                  <div
                    className={
                      m.role === "user"
                        ? "max-w-[80%] rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-[13px] leading-relaxed text-white"
                        : "max-w-[92%] rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-2.5 text-slate-800"
                    }
                  >
                    {m.role === "assistant" ? (
                      <Markdown text={m.content} />
                    ) : (
                      <span className="whitespace-pre-wrap break-words">{m.content}</span>
                    )}
                  </div>
                </div>
              ))}

              {pending && pending.length > 0 && (
                <div className="rounded-2xl rounded-bl-md border border-indigo-200 bg-indigo-50/50 px-4 py-3">
                  <div className="mb-2 text-[12.5px] text-slate-600">
                    计划已就绪，确认后开始执行
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={doConfirm}
                      disabled={busy}
                      className="rounded-lg bg-indigo-600 px-3 py-1.5 text-[12.5px] text-white transition hover:bg-indigo-700 disabled:opacity-50"
                    >
                      ✅ 确认执行
                    </button>
                    <button
                      onClick={doCancel}
                      disabled={busy}
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                    >
                      ↩️ 取消
                    </button>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className="border-t border-slate-200 bg-white px-6 py-3">
            <div className="mx-auto max-w-3xl">
              <div className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 focus-within:border-indigo-300">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                  rows={2}
                  placeholder="输入自然语言指令，回车发送（Shift+回车换行）"
                  className="max-h-32 min-h-[44px] w-full resize-none border-0 bg-transparent text-[13px] leading-relaxed outline-none placeholder:text-slate-400"
                />
                <button
                  onClick={send}
                  disabled={busy || !input.trim()}
                  className="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-[13px] font-medium text-white transition hover:bg-indigo-700 disabled:opacity-40"
                >
                  发送
                </button>
              </div>
              <label className="mt-2 flex w-fit cursor-pointer items-center gap-1.5 text-[12px] text-slate-500">
                <input
                  type="checkbox"
                  checked={skipConfirm}
                  onChange={(e) => setSkipConfirm(e.target.checked)}
                  className="h-3.5 w-3.5 accent-indigo-600"
                />
                跳过确认（直接执行）
              </label>
            </div>
          </div>
        </main>

        {/* ---------------- 右栏：3D 视图（默认收起） ---------------- */}
        {showView && (
          <aside className="flex w-[36%] shrink-0 flex-col gap-3 overflow-y-auto border-l border-slate-200 bg-white p-3">
            <div className="text-[12.5px] font-medium text-slate-600">🗺️ 3D 视图</div>
            <select
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[12px]"
            >
              {figures.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.name}
                </option>
              ))}
            </select>
            <div className="text-[11px] text-slate-400">{figLabel}</div>

            {/* 弹窗选择本地 html 文件（浏览器不允许网页拿到真实路径，所以传文件内容） */}
            <input
              ref={fileInputRef}
              type="file"
              accept=".html,text/html"
              multiple
              className="hidden"
              onChange={onPickFiles}
            />
            <div className="flex gap-2">
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12px] text-slate-700 transition hover:border-indigo-300 hover:text-indigo-600 disabled:opacity-50"
              >
                {uploading ? "导入中…" : "📄 选择本地 HTML"}
              </button>
              <button
                onClick={() => setShowPathInput((v) => !v)}
                className="shrink-0 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12px] text-slate-500 hover:border-indigo-300"
                title="也可以直接输入服务器上的文件夹路径"
              >
                路径
              </button>
            </div>
            {figMsg && (
              <div className="break-words text-[11.5px] text-slate-500">{figMsg}</div>
            )}

            {showPathInput && (
              <div className="flex gap-2">
                <input
                  id="folder-input"
                  placeholder="服务器上的文件夹路径"
                  className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1.5 text-[12px]"
                />
                <button
                  onClick={async () => {
                    const el = document.getElementById("folder-input") as HTMLInputElement | null;
                    if (!el?.value.trim()) return;
                    try {
                      const r = await api.addFolder(el.value.trim());
                      el.value = "";
                      setFigMsg(`✅ 已加入 ${r.added} 个 html（来自该文件夹）`);
                      await refreshFigures();
                    } catch (e) {
                      setFigMsg(`❌ ${e instanceof Error ? e.message : String(e)}`);
                    }
                  }}
                  className="shrink-0 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12px] text-slate-600 hover:border-indigo-300"
                >
                  扫描
                </button>
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
              {current ? (
                <iframe
                  title="3D 视图"
                  src={figureSrc(current)}
                  className="h-full min-h-[520px] w-full border-0"
                />
              ) : (
                <div className="p-6 text-center text-[12px] text-slate-400">
                  还没有 3D 图
                </div>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

/** LLM 设置折叠区（key 只显示脱敏值，留空表示不修改） */
function LlmBox({ onSaved }: { onSaved: (header: string) => void }) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [masked, setMasked] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.llmConfig().then((c) => {
      setBaseUrl(c.base_url);
      setModel(c.model);
      setMasked(c.api_key_masked);
    }).catch(() => {});
  }, []);

  const save = async () => {
    setBusy(true);
    try {
      const r = await api.saveLlmConfig({ api_key: apiKey, base_url: baseUrl, model });
      setMsg(r.message);
      onSaved(r.header);
      setApiKey("");
    } catch (e) {
      setMsg(`❌ ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const field =
    "w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[12px] outline-none focus:border-indigo-300";

  return (
    <div className="space-y-2 px-3 pb-3">
      <div>
        <label className="mb-1 block text-[11.5px] text-slate-500">API Key（留空不改）</label>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={masked || "未配置"}
          className={field}
        />
      </div>
      <div>
        <label className="mb-1 block text-[11.5px] text-slate-500">Base URL</label>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={field} />
      </div>
      <div>
        <label className="mb-1 block text-[11.5px] text-slate-500">模型名</label>
        <input value={model} onChange={(e) => setModel(e.target.value)} className={field} />
      </div>
      <button
        onClick={save}
        disabled={busy}
        className="w-full rounded-lg bg-slate-800 px-3 py-1.5 text-[12.5px] text-white transition hover:bg-slate-900 disabled:opacity-50"
      >
        保存并生效
      </button>
      {msg && <div className="break-words text-[11.5px] text-slate-600">{msg}</div>}
    </div>
  );
}