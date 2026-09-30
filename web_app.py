"""网页内核：Gradio 聊天界面 + 3D 视图预览。

只调用 agent_cli.Agent 的公开接口（Agent / handle），不改动 agent/ 与 skills/。

运行：
  python web_app.py        # 浏览器访问
  desktop_app.py 会复用本文件的 build_app()
"""

import html as html_mod
import os
import time
from pathlib import Path

import gradio as gr

from agent_cli import Agent, LLMConfigError

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"


# ---------- Agent 初始化（仅一次，失败不崩溃，界面给出提示） ----------

_agent = None
_agent_error = None
try:
    run_dir = OUTPUT_DIR / time.strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    _agent = Agent(mock=False, run_dir=run_dir)
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


def _find_view3d_path():
    """取最新的 view_3d.html 路径；找不到返回 None。

    优先用 session.outputs 里记录的路径，其次递归搜 output/ 下最新的。
    """
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
    """扫描 output/ 及其一级子目录（run_* 等）下的 .html，按修改时间倒序。

    返回 [(相对 output/ 的路径, 修改时间戳)]，供下拉框展示。
    """
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
    rel = full.relative_to(ROOT).as_posix()
    return (
        # gradio 6 的静态文件路由为 /gradio_api/file=（旧版为 /file=）
        f'<iframe src="/gradio_api/file={rel}" style="width:100%;height:720px;'
        'border:1px solid #ddd;border-radius:8px;background:#fff;"'
        ' title="3D 视图"></iframe>'
    )


# ---------- 聊天回调：生成器逐步推送，避免界面卡死 ----------

def respond(user_text, history, current_pick):
    """处理一条指令。先推"执行中…"，结束后推真实回复与 3D 视图。"""
    user_text = (user_text or "").strip()
    if not user_text:
        yield history, gr.update(), gr.update()
        return

    _before = _scan_html_files()
    before_top = _before[0][0] if _before else None

    history = list(history or [])
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": "⏳ 执行中…"})
    yield history, gr.update(), gr.update()

    if _agent is None:
        history[-1] = {"role": "assistant",
                       "content": f"❌ {_agent_error or 'Agent 不可用'}"}
        yield history, gr.update(), gr.update()
        return

    try:
        ok, message = _agent.handle(user_text)
    except Exception as e:  # noqa: BLE001
        ok, message = False, f"执行异常: {e}"

    icon = "✅" if ok else "❌"
    history[-1] = {"role": "assistant", "content": f"{icon} {message}"}

    # 只有本次真的产出了新图才切过去；否则保留用户当前选中的历史图
    _after = _scan_html_files()
    choices = [name for name, _ in _after]
    top = choices[0] if choices else None
    label = _label_with_count(len(choices))
    if top is not None and top != before_top:
        yield history, _iframe_for(top), gr.update(choices=choices, value=top,
                                                   label=label)
    elif choices:
        keep = current_pick if current_pick in choices else top
        yield history, gr.update(), gr.update(choices=choices, value=keep,
                                              label=label)
    else:
        yield history, gr.update(), gr.update(choices=[], value=None, label=label)


# ---------- 界面 ----------

def build_app():
    with gr.Blocks(title="SpatialZ 3D 组织重构智能体") as demo:
        gr.HTML(_header_html())
        with gr.Row():
            with gr.Column(scale=1):
                chatbot = gr.Chatbot(
                    label="聊天记录", height=520)  # gradio 6 默认即 messages 格式
                with gr.Row():
                    txt = gr.Textbox(
                        placeholder="输入自然语言指令，如：加载 data 下的 151673 切片",
                        label="输入", scale=4, autofocus=True)
                    send_btn = gr.Button("发送", variant="primary", scale=1)
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

        inputs = [txt, chatbot, picker]
        outputs = [chatbot, view, picker]
        send_btn.click(respond, inputs=inputs, outputs=outputs)
        txt.submit(respond, inputs=inputs, outputs=outputs)
        # 下拉框选中 → 立即加载该图；刷新按钮 → 重新扫描
        picker.change(_iframe_for, inputs=[picker], outputs=[view])
        refresh_btn.click(_dropdown_update, inputs=[], outputs=[picker])
    return demo


demo = build_app()

if __name__ == "__main__":
    # queue 保证长任务期间界面不卡死；allowed_paths 允许 iframe 读取 output/ 下的视图文件
    demo.queue().launch(allowed_paths=[str(OUTPUT_DIR)])
