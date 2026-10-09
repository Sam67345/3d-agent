"""M13 API 服务层：把现有 Agent 能力暴露成 HTTP 接口，供 React 前端调用。

设计原则（铁律：旧版一行不改）：
- **薄封装**：直接 import web_app，复用它的 Agent 实例、面板/会话/图扫描/LLM 配置逻辑，
  不复制、不改写任何核心逻辑。web_app.py 顶部只是构建 UI，本模块不碰那些 UI 函数。
- 只做三件事：HTTP 端点、路径安全校验、把同步长任务跑在线程池里（FastAPI 的同步端点
  本身就在线程池执行，所以直接 def 即可，不阻塞事件循环）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import web_app as W

app = FastAPI(title="SpatialZ 3D Agent API", version="M13")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001", "http://127.0.0.1:3001",
        # vite 有时会用 127.0.0.1/localhost 交替，放行本地所有端口便于调试
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 手动添加的历史图目录（与 web_app 的 _EXTRA_DIRS 共享同一份状态）
_extra_dirs = W._EXTRA_DIRS


# ---------------- 请求体模型 ----------------

class ChatReq(BaseModel):
    message: str = Field(..., description="自然语言指令")
    skip_confirm: bool = Field(False, description="跳过计划确认，直接执行")


class ConfirmReq(BaseModel):
    steps: list = Field(..., description="已确认的计划步骤（原样回传）")


class SessionRestoreReq(BaseModel):
    path: str = Field(..., description="会话 json 文件路径")


class FolderReq(BaseModel):
    folder: str = Field(..., description="要加入扫描的文件夹路径")


class LLMConfigReq(BaseModel):
    api_key: str = Field("", description="留空表示不改")
    base_url: str = Field("", description="留空表示不改")
    model: str = Field("", description="留空表示不改")


# ---------------- 路径安全 ----------------

def _safe_figure_path(rel_path: str) -> Path:
    """把前端传来的相对路径解析成绝对路径，并限制在允许的目录内。

    允许范围：OUTPUT_DIR（含一级子目录）+ 用户手动添加的目录。
    解析后必须 realpath 落在允许目录内，否则 403——防任意文件读取。
    """
    if not rel_path:
        raise HTTPException(400, "path 为空")
    # 允许前端传相对 output/ 的路径，或直接传绝对路径
    candidate = Path(rel_path)
    if not candidate.is_absolute():
        candidate = W.OUTPUT_DIR / rel_path
    try:
        real = candidate.resolve()
    except OSError:
        raise HTTPException(400, "路径无法解析")

    allowed = [W.OUTPUT_DIR.resolve()] + [d.resolve() for d in _extra_dirs]
    for base in allowed:
        try:
            real.relative_to(base)
            break
        except ValueError:
            continue
    else:
        raise HTTPException(403, f"不允许读取该路径: {rel_path}")
    if not real.exists() or not real.is_file():
        raise HTTPException(404, f"文件不存在: {rel_path}")
    return real


# ---------------- 助手 ----------------

def _agent_ready():
    if W._agent is None:
        raise HTTPException(503, W._agent_error or "Agent 未就绪（请先配置 LLM）")


def _mask_key(key: str) -> str:
    """脱敏：只保留末 4 位。绝不返回完整 key。"""
    if not key:
        return ""
    return ("*" * max(0, len(key) - 4)) + key[-4:]


# ---------------- 端点：对话 ----------------

@app.post("/api/chat")
def api_chat(req: ChatReq):
    """规划 + （可选）执行。skip_confirm=True 时一路执行完；否则只返回计划待确认。"""
    _agent_ready()
    msg = (req.message or "").strip()
    if not msg:
        raise HTTPException(400, "message 为空")

    steps, reply, dropped = W._agent.prepare_steps(msg)
    if not steps:
        return {"status": "clarify", "reply": reply or "没有可执行步骤，请补充信息",
                "steps": [], "panel": W._panel_md()}
    if not req.skip_confirm:
        return {"status": "confirm", "reply": reply, "steps": steps,
                "plan_card": W._plan_card(steps, reply), "panel": W._panel_md()}

    ok, message = W._agent.run_steps(steps)
    if ok:
        W._save_session(load_args=next(
            (s.get("args") for s in steps if s["skill"] == "load_slices"), None),
            history=[{"role": "user", "content": msg}])
    return {"status": "done" if ok else "error", "reply": message,
            "steps": steps, "dropped": dropped, "panel": W._panel_md()}


@app.post("/api/confirm")
def api_confirm(req: ConfirmReq):
    """用户点了【确认执行】：按已确认的步骤执行。"""
    _agent_ready()
    ok, message = W._agent.run_steps(req.steps)
    if ok:
        W._save_session(load_args=next(
            (s.get("args") for s in req.steps if s["skill"] == "load_slices"), None))
    return {"status": "done" if ok else "error", "reply": message,
            "panel": W._panel_md()}


@app.post("/api/cancel")
def api_cancel():
    """用户点了【取消】：丢弃待确认计划。"""
    return {"status": "cancelled", "reply": "已取消，等待下一条指令。",
            "panel": W._panel_md()}


# ---------------- 端点：面板 / 会话 ----------------

@app.get("/api/panel")
def api_panel():
    """切片信息面板：复用 web_app 的面板数据源（session.pair_meta 等）。"""
    s = W._agent.session if W._agent else None
    meta = (s.pair_meta or []) if s else []
    return {
        "markdown": W._panel_md(),
        "slices": [{"sample_id": m.get("sample_id"), "n_spots": m.get("n_spots"),
                    "n_genes": m.get("n_genes")} for m in meta],
        "n_virtual": len((s.virtual_slices or []) if s else []),
        "run_dir": str(getattr(s, "run_dir", "") or "") if s else "",
    }


@app.get("/api/sessions")
def api_sessions():
    items = W._list_sessions()
    out = []
    for name, path, data in items:
        out.append({
            "title": data.get("title") or "未命名会话",
            "saved_at": data.get("saved_at", ""),
            "n_slices": len(data.get("slice_meta") or []),
            "n_virtual": data.get("n_virtual", 0),
            "path": str(path),
            "label": name,
        })
    return {"sessions": out,
            "latest_exists": W.SESSION_JSON.exists(),
            "latest_saved_at": (W._session_summary() or {}).get("saved_at")}


@app.post("/api/sessions/new")
def api_sessions_new():
    """新建会话：归档当前 session_latest.json，再重置会话状态（逻辑同 Gradio 版）。"""
    archived = W._archive_current_session()
    W._session_title = None
    if W._agent is not None:
        from agent.session import Session
        run_dir = W.OUTPUT_DIR / time.strftime("run_%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        W._agent.session = Session()
        W._agent.session.run_dir = run_dir
        W._agent.executor.session = W._agent.session   # executor 持有旧引用，必须同步
    return {"status": "ok",
            "archived": Path(archived).name if archived else None,
            "sessions": api_sessions()["sessions"],
            "panel": W._panel_md()}


@app.post("/api/sessions/restore")
def api_sessions_restore(req: SessionRestoreReq):
    """按会话文件恢复：走与 Gradio 版完全相同的 _restore_from 逻辑。"""
    _agent_ready()
    p = Path(req.path)
    # 路径校验：只允许 output/ 及其子目录（含 sessions/）里的 json
    try:
        real = p.resolve()
        real.relative_to(W.OUTPUT_DIR.resolve())
    except (OSError, ValueError):
        raise HTTPException(403, "会话文件必须位于 output/ 目录内")
    data = W._session_summary(real)
    if not data:
        raise HTTPException(404, "会话文件不存在或无法解析")
    ok, msg = W._restore_from(data)
    W._session_title = data.get("title")
    if ok:
        try:
            W.SESSION_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        except OSError:
            pass
    return {"status": "ok" if ok else "error", "reply": msg,
            "panel": W._panel_md()}


# ---------------- 端点：图 ----------------

@app.get("/api/figures")
def api_figures():
    """扫描 output/ 与用户手动添加目录下的 html（复用 web_app 的扫描逻辑）。"""
    entries = W._collect_entries()
    return {
        "figures": [{"name": n, "path": n, "abs": str(p), "mtime": m}
                    for n, p, m in entries],
        "label": W._label_with_count(len(W._scan_html_files()),
                                     sum(1 for n, _, _ in entries if n.startswith("📁"))),
    }


@app.post("/api/figures/folder")
def api_figures_folder(req: FolderReq):
    """把用户指定的文件夹加入扫描范围（追加，不顶掉默认 output/）。"""
    folder = Path(req.folder.strip().strip('"'))
    if not folder.exists():
        raise HTTPException(404, f"文件夹不存在: {folder}")
    if not folder.is_dir():
        raise HTTPException(400, f"不是文件夹: {folder}")
    found = W._scan_extra_dir(folder)
    if folder not in _extra_dirs:
        _extra_dirs.append(folder)
    return {"status": "ok", "added": len(found), "figures": api_figures()["figures"]}


@app.get("/api/figures/file")
def api_figures_file(path: str):
    """返回指定 html 文件内容（有路径校验：只允许 output/ 与手动添加的目录内）。

    必须用 HTMLResponse：直接返回 str 会被 FastAPI 序列化成 JSON 字符串，
    浏览器 iframe 拿到的是带引号的 JSON 而不是 HTML，图就渲染不出来。
    """
    real = _safe_figure_path(path)
    try:
        content = real.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(500, f"读取失败: {e}")
    return HTMLResponse(content=content)


# ---------------- 端点：LLM 配置 ----------------

@app.get("/api/llm-config")
def api_llm_config_get():
    """读取配置。**只返回脱敏后的 key**（末 4 位），绝不返回完整密钥。"""
    env = W._read_env_file()
    key = env.get("MY_LLM_API_KEY", "")
    return {
        "base_url": env.get("MY_LLM_BASE_URL", ""),
        "model": env.get("MY_LLM_MODEL", ""),
        "api_key_masked": _mask_key(key),
        "has_key": bool(key),
    }


@app.post("/api/llm-config")
def api_llm_config_set(req: LLMConfigReq):
    """写 .env 并热切换（复用 web_app 的 _apply_llm_config）。"""
    env = W._read_env_file()
    api_key = req.api_key.strip() or env.get("MY_LLM_API_KEY", "")
    base_url = req.base_url.strip() or env.get("MY_LLM_BASE_URL", "")
    model = req.model.strip() or env.get("MY_LLM_MODEL", "")
    ok, msg = W._apply_llm_config(api_key, base_url, model)
    if not ok:
        raise HTTPException(400, msg)
    return {"status": "ok", "message": msg, "header": W._header_html(),
            "config": api_llm_config_get()}


@app.get("/api/health")
def api_health():
    return {"status": "ok", "agent": W._agent is not None,
            "error": W._agent_error, "time": time.strftime("%Y-%m-%d %H:%M:%S")}


if __name__ == "__main__":
    # 7861 与 Gradio 的 7860 错开；host 用 127.0.0.1 只监听本机
    uvicorn.run(app, host="127.0.0.1", port=7861, log_level="warning")