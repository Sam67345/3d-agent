"""网页内核：Gradio 聊天界面 + 3D 视图预览 + M8 交互闭环三件套。

M8 三件套：
1. 计划确认卡片：先出计划步骤表，用户点【确认执行】才跑（可勾选"跳过确认"）
2. 步骤流式展示：每步开始/完成/被拦截/重试都实时推进聊天区
3. 结果解读：validate 返回后追加纯代码判定的中文解读（不调用 LLM，防吹牛）

只调用 agent_cli.Agent 的公开接口（prepare_steps / run_steps / handle），
不改动 agent/ 与 skills/。

运行：
  python web_app.py        # 浏览器访问
  desktop_app.py 会复用本文件的 build_app()
"""

import html as html_mod
import json
import os
import queue
import re
import threading
import time
import urllib.parse
from pathlib import Path

import gradio as gr

from agent_cli import Agent, LLMConfigError
from agent.executor import Executor as _BaseExecutor

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SESSION_JSON = OUTPUT_DIR / "session_latest.json"

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
_ALPHA_RE = re.compile(r"alpha([0-9]*\.?[0-9]+)")


# ---------- Agent 初始化（仅一次，失败不崩溃，界面给出提示） ----------

class _UIExecutor(_BaseExecutor):
    """把 Executor 的 print 日志转给 UI（不改 agent/，用子类覆写 _emit）。"""

    sink = None

    def _emit(self, msg):
        if self.verbose:
            print(msg)
        if self.sink:
            self.sink(msg)


_agent = None
_agent_error = None
try:
    run_dir = OUTPUT_DIR / time.strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    _agent = Agent(mock=False, run_dir=run_dir)
    _agent.executor = _UIExecutor(_agent.session, llm=_agent.llm, verbose=True)
except LLMConfigError as e:
    _agent_error = f"Agent 初始化失败：{e}"


def _header_html():
    """页面顶部一行：当前 LLM 模型；未配置则红色警告。"""
    model = os.environ.get("MY_LLM_MODEL")
    if model:
        return (
            '<div style="padding:8px 12px;border-radius:8px;'
            'background:#eef6ff;border:1px solid #cfe3ff;'
            'font-size:15px;color:#1a3b5d;">'
            f'当前 LLM 模型 = <b>{html_mod.escape(model)}</b></div>'
        )
    return (
        '<div style="padding:8px 12px;border-radius:8px;'
        'background:#fdecea;border:1px solid #f5c6c2;'
        'font-size:15px;color:#b02a22;font-weight:600;">'
        '⚠️ 未配置 LLM（缺少环境变量 MY_LLM_MODEL）</div>'
    )


# ---------- 历史 3D 图下拉框 ----------

def _find_view3d_path():
    """取最新的 view_3d.html 路径；找不到返回 None。"""
    candidates = []
    if _agent is not None:
        p = _agent.session.outputs.get("view_3d")
        if p:
            candidates.append(Path(p))
    if OUTPUT_DIR.exists():
        candidates.extend(sorted(OUTPUT_DIR.rglob("view_3d.html"),
                                 key=lambda p: p.stat().st_mtime,
                                 reverse=True))
    for p in candidates:
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


PLACEHOLDER = (
    '<div style="color:#888;padding:24px;text-align:center;">'
    '还没有 3D 图。执行「画 3D 图」，或从上方下拉框选择历史生成的 HTML</div>'
)


def _scan_html_files():
    """扫描 output/ 及其一级子目录（run_* 等）下的 .html，按修改时间倒序。"""
    if not OUTPUT_DIR.exists():
        return []
    files = list(OUTPUT_DIR.glob("*.html"))
    files += [p for sub in OUTPUT_DIR.iterdir() if sub.is_dir()
              for p in sub.glob("*.html")]
    scored = []
    for p in files:
        try:
            scored.append((p.relative_to(OUTPUT_DIR).as_posix(), p.stat().st_mtime))
        except OSError:
            continue
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _label_with_count(n):
    return f"历史 3D 图（output/ 及其一级子目录，共 {n} 个）"


def _dropdown_update(newest=None):
    """重新扫描清单，返回 gr.update（默认选中最新，或指定项）。"""
    files = _scan_html_files()
    choices = [name for name, _ in files]
    update = {"label": _label_with_count(len(choices))}
    if not choices:
        return gr.update(choices=[], value=None, **update)
    value = newest if newest in choices else choices[0]
    return gr.update(choices=choices, value=value, **update)


def _iframe_for(rel_path):
    """把选中的 html 文件包进 iframe（gr.HTML 直接注入的 <script> 不会执行，
    iframe 加载完整 HTML 文件则 plotly 可正常渲染与旋转）。"""
    if not rel_path:
        return PLACEHOLDER
    full = OUTPUT_DIR / rel_path
    if not full.exists():
        return PLACEHOLDER
    # gradio 的 file 路由要求绝对路径（相对路径会 404 → iframe 空白），
    # 且路径必须 URL 编码（含中文目录名时必须）
    encoded = urllib.parse.quote(str(full.resolve()), safe=":/\\")
    return (
        # gradio 6 的静态文件路由为 /gradio_api/file=（旧版为 /file=）
        f'<iframe src="/gradio_api/file={encoded}" style="width:100%;height:720px;'
        'border:1px solid #ddd;border-radius:8px;background:#fff;"'
        ' title="3D 视图"></iframe>'
    )


def _finish_view_update(before_top, current_pick):
    """执行结束后：只有真的产出新图才切换，否则保留用户当前选择。"""
    _after = _scan_html_files()
    choices = [name for name, _ in _after]
    top = choices[0] if choices else None
    label = _label_with_count(len(choices))
    if top is not None and top != before_top:
        return _iframe_for(top), gr.update(choices=choices, value=top, label=label)
    if choices:
        keep = current_pick if current_pick in choices else top
        return gr.update(), gr.update(choices=choices, value=keep, label=label)
    return gr.update(), gr.update(choices=[], value=None, label=label)


# ---------- M8-3：结果解读（纯代码判定，不调用 LLM） ----------

def _explain_validate(result):
    """把 validate 的相邻层 Spearman 结果翻成一句中文判断。

    规则写死：全部 >= 0.95 → 重建质量良好；有 < 0.9 → 点名偏低处并建议检查；
    落在 0.9~0.95 → 整体可接受但个别层略偏。
    """
    if not isinstance(result, dict):
        return None
    rhos = result.get("adjacent_spearman_rho")
    if not rhos:
        return None
    try:
        rhos = [float(r) for r in rhos]
    except (TypeError, ValueError):
        return None

    parts = ["📊 **结果解读**（相邻层 Spearman 相关性）"]
    parts.append("  \n" + "、".join(
        f"第 {i+1} 处 **{r:.4f}**" for i, r in enumerate(rhos)))
    low = [i + 1 for i, r in enumerate(rhos) if r < 0.9]
    if all(r >= 0.95 for r in rhos):
        parts.append("  \n✅ 全部 ≥ 0.95 → **重建质量良好**。")
    elif low:
        parts.append("  \n⚠️ 第 " + "、".join(str(i) for i in low)
                     + " 处相关性偏低（< 0.9），建议检查该处的插值参数或切片对齐。")
    else:
        parts.append("  \n🟡 存在 0.9~0.95 之间的层：整体可接受，但个别层略有偏差。")
    return "".join(parts)


# ---------- M9-1：会话保存与恢复 ----------

def _virtual_slice_paths():
    """session.outputs 里指向虚拟切片 h5ad 的路径（数据本体已在 output/ 下）。"""
    out = []
    for k, v in (_agent.session.outputs or {}).items():
        if not v or not str(v).endswith(".h5ad"):
            continue
        if k.startswith("virtual_alpha") or "-alpha" in k:
            out.append(str(v))
    return out


def _save_session(load_args=None, history=None):
    """每次成功执行后落盘：只存路径引用与元信息，不复制 h5ad 数据本体。"""
    if _agent is None:
        return
    s = _agent.session
    prev = {}
    if SESSION_JSON.exists():
        try:
            prev = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    data = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_dir": str(s.run_dir) if getattr(s, "run_dir", None) else None,
        # 记录 load_slices 的调用参数，恢复时原样交给现有技能重跑
        "load_args": load_args or prev.get("load_args"),
        "slice_meta": s.pair_meta or [],
        "virtual_slice_paths": _virtual_slice_paths() or prev.get(
            "virtual_slice_paths", []),
        "outputs": {k: str(v) for k, v in (s.outputs or {}).items()},
        "n_virtual": len(s.virtual_slices or []),
        "chat_summary": [
            {"role": m.get("role"), "content": str(m.get("content"))[:160]}
            for m in (history or [])[-6:]],
    }
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except OSError as e:
        print(f"[session] 保存失败: {e}")


def _session_summary():
    """读回 json，给启动提示用；没有则返回 None。"""
    if not SESSION_JSON.exists():
        return None
    try:
        return json.loads(SESSION_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _startup_notice():
    """启动提示文案：时间、已加载切片、虚拟切片层数。"""
    data = _session_summary()
    if not data:
        return None
    names = [m.get("sample_id") for m in (data.get("slice_meta") or [])]
    names = [n for n in names if n]
    return ("💾 **检测到上次会话**（"
            f"{data.get('saved_at', '未知时间')}"
            + (f"，已加载切片：{', '.join(names)}" if names else "，未记录切片")
            + f"，已有 {data.get('n_virtual', 0)} 层虚拟切片）  \n"
            "点【恢复会话】继续，或点【开始新会话】从零开始。")


def _restore_session():
    """恢复：调用现有 load_slices 技能重放加载，虚拟切片读回已存在的 h5ad。

    返回 (ok, message)；失败时给出原因，由调用方降级为新会话。
    """
    data = _session_summary()
    if not data:
        return False, "没有找到会话文件 output/session_latest.json"
    args = data.get("load_args") or {}
    if not args:
        return False, "会话记录里没有 load_slices 参数，无法恢复"

    try:
        ok, msg = _agent.executor.run_step("load_slices", dict(args))
    except Exception as e:  # noqa: BLE001
        return False, f"重放 load_slices 失败: {type(e).__name__}: {e}"
    if not ok:
        return False, f"重放 load_slices 失败: {msg}"

    # 虚拟切片：读回 output/ 下已存在的 h5ad 本体（不重新计算）
    import anndata as ad
    n = 0
    for p in data.get("virtual_slice_paths") or []:
        try:
            if Path(p).exists():
                _agent.session.virtual_slices.append(ad.read_h5ad(p))
                n += 1
        except Exception as e:  # noqa: BLE001
            print(f"[session] 恢复虚拟切片失败 {p}: {e}")
    if data.get("run_dir"):
        _agent.session.run_dir = Path(data["run_dir"])
    for k, v in (data.get("outputs") or {}).items():
        _agent.session.outputs.setdefault(k, v)
    return True, (f"已恢复会话：{len(_agent.session.all_slices)} 张真实切片、"
                  f"{n} 层虚拟切片")


# ---------- M9-2：切片信息面板 ----------

def _panel_md():
    """常驻信息面板：已加载切片 / 虚拟切片层数与 alpha / 输出目录。

    数据全部取自 session（load_slices 写的 meta），不做任何推断或编造。
    """
    if _agent is None:
        return "### 📊 切片信息面板\n（Agent 未就绪）"
    s = _agent.session
    lines = ["### 📊 切片信息面板"]

    meta = s.pair_meta or []
    if meta:
        lines.append("**已加载切片**")
        lines.append("| 名称 | spots | 基因 |")
        lines.append("|---|---:|---:|")
        for m in meta:
            lines.append(f"| {m.get('sample_id', '-')} | {m.get('n_spots', '-')} "
                         f"| {m.get('n_genes', '-')} |")
    else:
        lines.append("**已加载切片**：无（先说「加载 …」）")

    vs = s.virtual_slices or []
    if vs:
        alphas = []
        for v in vs:
            sid = ""
            try:
                sid = str(v.obs["slice_id"].iloc[0])
            except (KeyError, IndexError, AttributeError):
                sid = ""
            m = _ALPHA_RE.search(sid)
            alphas.append(m.group(1) if m else (sid or "?"))
        lines.append(f"**虚拟切片**：{len(vs)} 层（alpha {('、'.join(alphas))}）")
    else:
        lines.append("**虚拟切片**：0 层")

    run_dir = getattr(s, "run_dir", None)
    lines.append(f"**输出目录**：`{run_dir}`" if run_dir else "**输出目录**：未设置")
    return "\n".join(lines)


# ---------- M8-1：计划卡片文本 ----------

def _fmt_args(args):
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _plan_card(steps, reply=""):
    """把步骤表渲染成聊天区的一段文字。"""
    lines = [f"📋 **我将执行 {len(steps)} 个步骤：**"]
    for i, s in enumerate(steps):
        num = CIRCLED[i] if i < len(CIRCLED) else f"({i+1})"
        args = _fmt_args(s.get("args", {}))
        head = f"{num} `{s['skill']}`" + (f"（{args}）" if args else "")
        reason = s.get("reason")
        lines.append(head + (f" — {reason}" if reason else ""))
    if reply:
        lines.append(f"  \n💬 {reply}")
    lines.append("  \n点【确认执行】开始，或点【取消】重新输入。")
    return "  \n".join(lines)


# ---------- M8-2：步骤流式执行 ----------

_NOISY_PREFIX = ("[retry", "[error", "[normalize", "[wire")


def _execute_steps(steps, history, before_top, current_pick):
    """后台线程执行 + 主线程按事件流式推进聊天区。"""
    q = queue.Queue()
    results = []      # [(skill, result_dict)]
    logs = []

    def sink(msg):
        q.put({"type": "log", "msg": msg})

    _agent.executor.sink = sink

    def worker():
        try:
            ok, message = _agent.run_steps(steps, on_event=lambda ev: q.put(ev))
        except Exception as e:  # noqa: BLE001
            q.put({"type": "crash", "msg": str(e)})
            return
        q.put({"type": "end", "ok": ok, "message": message})

    threading.Thread(target=worker, daemon=True).start()

    history = list(history)
    history.append({"role": "assistant", "content": "🚀 开始执行…"})
    yield history, gr.update(), gr.update(), _panel_md()
    # 记录本次计划里的 load_slices 参数，成功后写进会话文件供恢复用
    load_args = next((s.get("args") for s in steps if s["skill"] == "load_slices"),
                     None)

    finished = False
    deadline = time.time() + 3600  # 兜底：最多等 1 小时，避免线程静默退出时空转
    while not finished and time.time() < deadline:
        try:
            ev = q.get(timeout=0.5)
        except queue.Empty:
            continue
        kind = ev.get("type")
        if kind == "end":
            finished = True
            ok, message = ev.get("ok"), ev.get("message")
        elif kind == "crash":
            finished = True
            ok, message = False, f"执行异常: {ev.get('msg')}"
        elif kind == "log":
            msg = str(ev.get("msg", "")).strip()
            if any(p in msg for p in _NOISY_PREFIX):
                logs.append(msg)
        elif kind == "step_start":
            logs = []
            history.append({"role": "assistant",
                            "content": f"⚙️ 正在执行 `{ev['skill']}`…"})
        elif kind == "step_done":
            sec = ev.get("elapsed_sec", 0)
            history[-1] = {"role": "assistant",
                           "content": f"✅ `{ev['skill']}` 完成（{sec} 秒）"}
            results.append((ev["skill"], ev.get("result") or {}))
        elif kind == "step_fail":
            history[-1] = {"role": "assistant",
                           "content": (f"❌ `{ev['skill']}` 失败："
                                       f"{ev.get('detail')}")}
        elif kind == "verifier_block":
            problems = ev.get("problems") or []
            history.append({"role": "assistant",
                            "content": ("🚫 Verifier 拦截 "
                                        f"`{ev['skill']}`："
                                        + "；".join(map(str, problems)))})
        # 重试/等日志实时挂在当前步骤消息下
        if logs and history:
            tail = "\n".join(f"<sub>{html_mod.escape(x)}</sub>" for x in logs[-4:])
            cur = history[-1]["content"]
            base = cur.split("\n<sub>")[0]
            history[-1] = {"role": "assistant",
                           "content": base + ("\n" + tail if tail else "")}
        yield history, gr.update(), gr.update(), _panel_md()

    # 收尾：总结 + 结果解读 + 视图刷新 + 自动保存会话
    history.append({"role": "assistant",
                    "content": ("✅ 全部完成" if ok else f"❌ {message}")})
    for skill, result in results:
        if skill == "validate":
            text = _explain_validate(result)
            if text:
                history.append({"role": "assistant", "content": text})
    view, drop = _finish_view_update(before_top, current_pick)
    _agent.executor.sink = None
    if ok:
        _save_session(load_args=load_args, history=history)
    yield history, view, drop, _panel_md()


# ---------- 聊天回调入口 ----------

def respond(user_text, history, current_pick, skip_confirm, pending):
    """发指令 → 先出计划卡（除非勾选跳过确认）→ 确认后执行。"""
    user_text = (user_text or "").strip()
    if not user_text:
        yield (history, gr.update(), gr.update(), gr.update(),
               gr.update(interactive=False), gr.update(interactive=False),
               gr.update())
        return

    history = list(history or [])
    history.append({"role": "user", "content": user_text})

    if _agent is None:
        history.append({"role": "assistant",
                        "content": f"❌ {_agent_error or 'Agent 不可用'}"})
        yield (history, gr.update(), gr.update(), None,
               gr.update(interactive=False), gr.update(interactive=False),
               gr.update())
        return

    history.append({"role": "assistant", "content": "🧠 正在规划…"})
    yield (history, gr.update(), gr.update(), gr.update(),
           gr.update(interactive=False), gr.update(interactive=False),
           _panel_md())

    try:
        steps, reply, _dropped = _agent.prepare_steps(user_text)
    except Exception as e:  # noqa: BLE001
        history[-1] = {"role": "assistant", "content": f"❌ 规划失败: {e}"}
        yield (history, gr.update(), gr.update(), None,
               gr.update(interactive=False), gr.update(interactive=False),
               _panel_md())
        return

    if not steps:
        history[-1] = {"role": "assistant",
                       "content": (reply or "❌ 没有可执行步骤，请换个说法或先加载数据")}
        yield (history, gr.update(), gr.update(), None,
               gr.update(interactive=False), gr.update(interactive=False),
               _panel_md())
        return

    if skip_confirm:
        history[-1] = {"role": "assistant",
                       "content": _plan_card(steps, reply) + "  \n（已跳过确认）"}
        before_top = _scan_html_files()
        before_top = before_top[0][0] if before_top else None
        for upd in _execute_steps(steps, history, before_top, current_pick):
            yield upd + (None, gr.update(interactive=False),
                         gr.update(interactive=False))
        return

    # 计划确认卡片：等用户点按钮
    history[-1] = {"role": "assistant", "content": _plan_card(steps, reply)}
    yield (history, gr.update(), gr.update(), steps,
           gr.update(interactive=True), gr.update(interactive=True),
           _panel_md())


def do_confirm(history, current_pick, pending):
    """用户点【确认执行】。"""
    if not pending:
        return (history, gr.update(), gr.update(), None,
                gr.update(interactive=False), gr.update(interactive=False),
                _panel_md())
    before = _scan_html_files()
    before_top = before[0][0] if before else None
    for upd in _execute_steps(pending, list(history or []), before_top,
                              current_pick):
        yield upd + (None, gr.update(interactive=False),
                     gr.update(interactive=False))


def do_cancel(history, pending):
    """用户点【取消】：清空待确认计划。"""
    history = list(history or [])
    history.append({"role": "assistant", "content": "↩️ 已取消，等待下一条指令。"})
    return (history, gr.update(), gr.update(), None,
            gr.update(interactive=False), gr.update(interactive=False),
            _panel_md())


def do_restore(history):
    """用户点【恢复会话】：重放 load_slices + 读回虚拟切片。"""
    history = list(history or [])
    history.append({"role": "assistant", "content": "💾 正在恢复上次会话…"})
    yield (history, gr.update(), gr.update(), None,
           gr.update(interactive=False), gr.update(interactive=False),
           _panel_md())
    ok, msg = _restore_session()
    history.append({"role": "assistant",
                    "content": (f"✅ {msg}" if ok else
                                f"❌ 恢复失败：{msg}\n已切换为新会话。")})
    yield (history, gr.update(), gr.update(), None,
           gr.update(interactive=False), gr.update(interactive=False),
           _panel_md())


def do_new_session(history):
    """用户点【开始新会话】：忽略旧会话记录。"""
    history = list(history or [])
    history.append({"role": "assistant",
                    "content": "🆕 已开始新会话（旧记录仍在 "
                               "output/session_latest.json，下次启动可再恢复）。"})
    return (history, gr.update(), gr.update(), None,
            gr.update(interactive=False), gr.update(interactive=False),
            _panel_md())


# ---------- 界面 ----------

def build_app():
    notice = _startup_notice()
    init_chat = []
    if notice:
        init_chat.append({"role": "assistant", "content": notice})

    with gr.Blocks(title="SpatialZ 3D 组织重构智能体") as demo:
        gr.HTML(_header_html())
        with gr.Row():
            with gr.Column(scale=1):
                chatbot = gr.Chatbot(
                    label="聊天记录", height=420, value=init_chat)
                with gr.Row():
                    txt = gr.Textbox(
                        placeholder="输入自然语言指令，如：加载 data 下的 151673 切片",
                        label="输入", scale=4, autofocus=True)
                    send_btn = gr.Button("发送", variant="primary", scale=1)
                with gr.Row():
                    skip = gr.Checkbox(label="跳过确认（--yes 等效）",
                                       value=False, scale=2)
                    confirm_btn = gr.Button("✅ 确认执行", variant="primary",
                                            interactive=False, scale=1)
                    cancel_btn = gr.Button("↩️ 取消", interactive=False, scale=1)
                with gr.Row():
                    restore_btn = gr.Button("💾 恢复会话", size="sm",
                                            interactive=bool(notice), scale=1)
                    new_btn = gr.Button("🆕 开始新会话", size="sm",
                                        interactive=bool(notice), scale=1)
                panel = gr.Markdown(value=_panel_md(), label="切片信息")
            with gr.Column(scale=1):
                _initial = _scan_html_files()
                _choices = [name for name, _ in _initial]
                picker = gr.Dropdown(
                    choices=_choices,
                    value=_choices[0] if _choices else None,
                    label=_label_with_count(len(_choices)),
                    allow_custom_value=False,
                    interactive=True,
                )
                with gr.Row():
                    refresh_btn = gr.Button("刷新列表", scale=1, size="sm")
                    hint = gr.Markdown(
                        "选中即可查看；每次对话生成新图后列表会自动刷新并把新图排在最前",
                        scale=4)
                view = gr.HTML(
                    value=_iframe_for(_choices[0] if _choices else None),
                    label="3D 视图")

        pending = gr.State(None)
        outputs = [chatbot, view, picker, pending, confirm_btn, cancel_btn, panel]
        send_btn.click(respond, inputs=[txt, chatbot, picker, skip, pending],
                       outputs=outputs)
        txt.submit(respond, inputs=[txt, chatbot, picker, skip, pending],
                   outputs=outputs)
        confirm_btn.click(do_confirm, inputs=[chatbot, picker, pending],
                          outputs=outputs)
        cancel_btn.click(do_cancel, inputs=[chatbot, pending], outputs=outputs)
        restore_btn.click(do_restore, inputs=[chatbot], outputs=outputs)
        new_btn.click(do_new_session, inputs=[chatbot], outputs=outputs)
        # 下拉框选中 → 立即加载该图；刷新按钮 → 重新扫描
        picker.change(_iframe_for, inputs=[picker], outputs=[view])
        refresh_btn.click(_dropdown_update, inputs=[], outputs=[picker])
    return demo


demo = build_app()

if __name__ == "__main__":
    # queue 保证长任务期间界面不卡死；allowed_paths 允许 iframe 读取 output/ 下的视图文件
    demo.queue().launch(allowed_paths=[str(OUTPUT_DIR)])
