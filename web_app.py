"""网页内核：Gradio 聊天界面 + 3D 视图预览 + M8 交互闭环 + M9 会话管理 + M11 界面完善。

M8 交互闭环三件套：
1. 计划确认卡片：先出计划步骤表，用户点【确认执行】才跑（可勾选"跳过确认"）
2. 步骤流式展示：每步开始/完成/被拦截/重试都实时推进聊天区
3. 结果解读：validate 返回后追加纯代码判定的中文解读（不调用 LLM，防吹牛）

M11：
- ⚙️ LLM 设置（API Key/Base URL/模型名）做进界面，写 .env 并热切换，无需重启
- 历史 3D 图可手动追加任意文件夹（默认仍扫 output/）
- 3D 查看器按生成图真实尺寸缩放，不再出现内部滚动条；布局压缩到 1400×900 一屏
- 会话列表：output/sessions/ 归档，界面可列出/切换/新建

只调用 agent/ 与 skills/ 的公开接口，不改动它们的现有行为。

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
import warnings
from pathlib import Path

import gradio as gr

from agent_cli import Agent, LLMConfigError
from agent.executor import Executor as _BaseExecutor
from agent.llm import LLMClient

# gradio 6 把 css 从 Blocks 迁到 launch，但仍需在 Blocks 上挂一份（desktop_app 复用 demo）
warnings.filterwarnings("ignore", message=".*moved from the Blocks constructor.*")

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SESSION_JSON = OUTPUT_DIR / "session_latest.json"
SESSIONS_DIR = OUTPUT_DIR / "sessions"
ENV_FILE = ROOT / ".env"

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
_ALPHA_RE = re.compile(r"alpha([0-9]*\.?[0-9]+)")

# M11-3：右栏视图的可用显示区（1400×900 窗口下的右栏约 660 宽）
VIEW_MAX_W = 660
VIEW_MAX_H = 470

# M11-2：手动追加的文件夹（默认扫描 output/ 之外的部分）
_EXTRA_DIRS: list[Path] = []
_CHOICE_MAP: dict[str, Path] = {}

# M11-4：当前会话标题（该会话第一条用户指令的前 20 字）
_session_title = None


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
            '<div style="padding:6px 12px;border-radius:8px;'
            'background:#eef6ff;border:1px solid #cfe3ff;'
            'font-size:14px;color:#1a3b5d;">'
            f'当前 LLM 模型 = <b>{html_mod.escape(model)}</b></div>'
        )
    return (
        '<div style="padding:6px 12px;border-radius:8px;'
        'background:#fdecea;border:1px solid #f5c6c2;'
        'font-size:14px;color:#b02a22;font-weight:600;">'
        '⚠️ 未配置 LLM（缺少环境变量 MY_LLM_MODEL），请在左侧「⚙️ LLM 设置」中填写'
        '</div>'
    )


# ---------- M11-1：LLM 配置（写 .env + 热切换） ----------

def _read_env_file():
    """读 .env 为 dict（不打印、不返回给界面）。"""
    data = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def _write_env_file(values):
    """把给定键值写回 .env，保留其它行与注释。"""
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    remaining = dict(values)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out).strip() + "\n", encoding="utf-8")


def _apply_llm_config(api_key, base_url, model):
    """把新配置写 .env + 写环境变量，并让当前 Agent 的 LLM 客户端热切换。

    返回 (ok, message)。message 里**绝不包含密钥**。
    """
    api_key = (api_key or "").strip()
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    missing = [n for n, v in (("API Key", api_key), ("Base URL", base_url),
                               ("模型名", model)) if not v]
    if missing:
        return False, f"❌ 未填写：{'、'.join(missing)}"

    _write_env_file({"MY_LLM_API_KEY": api_key,
                     "MY_LLM_BASE_URL": base_url,
                     "MY_LLM_MODEL": model})
    os.environ["MY_LLM_API_KEY"] = api_key
    os.environ["MY_LLM_BASE_URL"] = base_url
    os.environ["MY_LLM_MODEL"] = model

    global _agent, _agent_error
    try:
        client = LLMClient()
    except LLMConfigError as e:
        return False, f"❌ 配置无效：{e}"

    if _agent is None:
        # 启动时没有可用 Agent（例如首次使用没配 key）→ 现在补建
        try:
            run_dir = OUTPUT_DIR / time.strftime("run_%Y%m%d_%H%M%S")
            run_dir.mkdir(parents=True, exist_ok=True)
            _agent = Agent(mock=False, run_dir=run_dir)
            _agent.executor = _UIExecutor(_agent.session, llm=_agent.llm,
                                          verbose=True)
            _agent_error = None
            return True, (f"✅ 已保存并生效（已新建 Agent）。当前模型 = {model}；"
                          f"API Key 已写入 .env（{len(api_key)} 字符，不显示明文）")
        except Exception as e:  # noqa: BLE001
            return False, f"⚠️ 已保存 .env，但 Agent 初始化失败：{e}"

    # 热切换：三处引用同一个 LLM 实例（planner / executor 的错误修复都用它）
    _agent.llm = client
    _agent.planner.llm = client
    _agent.executor.llm = client
    return True, (f"✅ 已保存并即时生效，无需重启。当前模型 = {model}；"
                  f"API Key 已写入 .env（{len(api_key)} 字符，不显示明文）")


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
    """默认扫描：output/ 及其一级子目录（run_* 等）下的 .html，按修改时间倒序。

    返回 [(显示名, 绝对路径, mtime)]；显示名是相对 output/ 的路径（保持旧行为）。
    """
    if not OUTPUT_DIR.exists():
        return []
    files = list(OUTPUT_DIR.glob("*.html"))
    files += [p for sub in OUTPUT_DIR.iterdir() if sub.is_dir()
              for p in sub.glob("*.html")]
    scored = []
    for p in files:
        try:
            scored.append((p.relative_to(OUTPUT_DIR).as_posix(), p, p.stat().st_mtime))
        except OSError:
            continue
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored


def _scan_extra_dir(folder):
    """M11-2：扫描手动指定的文件夹（含子目录）下所有 .html。

    显示名带 📁 前缀标注来源，内容是相对该文件夹的路径。
    """
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(f"文件夹不存在: {folder}")
    if not root.is_dir():
        raise NotADirectoryError(f"这不是文件夹: {folder}")
    scored = []
    for p in root.rglob("*.html"):
        try:
            rel = p.relative_to(root).as_posix()
            scored.append((f"📁 {rel}", p, p.stat().st_mtime))
        except (OSError, ValueError):
            continue
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored


def _collect_entries():
    """下拉框的全部选项：默认 output/ 在前，手动文件夹追加在后（各自按时间倒序）。"""
    entries = list(_scan_html_files())
    for d in _EXTRA_DIRS:
        try:
            entries += _scan_extra_dir(d)
        except (OSError, ValueError):
            continue
    _CHOICE_MAP.clear()
    for name, path, _ in entries:
        _CHOICE_MAP[name] = path
    return entries


def _resolve_choice(choice):
    """显示名 → 绝对路径（默认项走 output/，手动项查映射表）。"""
    if not choice:
        return None
    if choice in _CHOICE_MAP:
        return _CHOICE_MAP[choice]
    return OUTPUT_DIR / choice


def _label_with_count(n, extra=0):
    base = f"历史 3D 图（output/ {n} 个"
    if extra:
        base += f" + 手动文件夹 {extra} 个"
    return base + "）"


def _dropdown_update(newest=None):
    """重新扫描清单，返回 gr.update（默认选中最新，或指定项）。"""
    entries = _collect_entries()
    choices = [name for name, _, _ in entries]
    update = {"label": _label_with_count(len(_scan_html_files()),
                                         sum(1 for c in choices if c.startswith("📁")))}
    if not choices:
        return gr.update(choices=[], value=None, **update)
    value = newest if newest in choices else choices[0]
    return gr.update(choices=choices, value=value, **update)


def _plotly_size(path):
    """从生成的 html 里读出 plotly 图的尺寸（ skills 固定 1100×800，这里自适应读取）。

    读不到就用 (1100, 800) 兜底。**不修改 skills/ 的生成逻辑**。
    """
    try:
        head = Path(path).read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return 1100, 800
    w = re.search(r"width\s*[:=]\s*(\d{3,4})", head)
    h = re.search(r"height\s*[:=]\s*(\d{3,4})", head)
    gw = int(w.group(1)) if w else 1100
    gh = int(h.group(1)) if h else 800
    return (gw if 200 <= gw <= 4000 else 1100), (gh if 200 <= gh <= 4000 else 800)


def _iframe_for(choice):
    """把选中的 html 包进 iframe，并按其真实尺寸等比缩放——避免 iframe 内出现滚动条。

    做法（不改 skills/ 的 html 生成）：外层容器固定显示尺寸并裁剪，内层 iframe 保持
    图的原始像素尺寸，再用 CSS transform: scale 缩放到容器内。代价是文字/点会被等比
    缩小，但换来「一屏完整可见、不需滚动」。
    """
    path = _resolve_choice(choice)
    if not path or not path.exists():
        return PLACEHOLDER
    # gradio 的 file 路由要求绝对路径（相对路径会 404 → iframe 空白），
    # 且路径必须 URL 编码（含中文目录名时必须）
    encoded = urllib.parse.quote(str(path.resolve()), safe=":/\\")
    gw, gh = _plotly_size(path)
    scale = min(1.0, VIEW_MAX_W / gw, VIEW_MAX_H / gh)
    box_w, box_h = int(gw * scale), int(gh * scale)
    return (
        f'<div style="width:100%;height:{box_h}px;overflow:hidden;'
        'border:1px solid #ddd;border-radius:8px;background:#fff;'
        'display:flex;align-items:center;justify-content:center;">'
        f'<iframe src="/gradio_api/file={encoded}" '
        f'style="width:{gw}px;height:{gh}px;border:0;flex:0 0 auto;'
        f'transform:scale({scale:.4f});transform-origin:center center;" '
        'title="3D 视图"></iframe></div>'
    )


def _finish_view_update(before_top, current_pick):
    """执行结束后：只有真的产出新图才切换，否则保留用户当前选择。"""
    entries = _collect_entries()
    choices = [name for name, _, _ in entries]
    top = choices[0] if choices else None
    label = _label_with_count(len(_scan_html_files()),
                              sum(1 for c in choices if c.startswith("📁")))
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
    # 会话标题优先级：当前会话已定的标题 > 本次聊天里的首条用户指令 > 旧记录里的标题
    # （否则新会话会被上一个会话的标题污染）
    title = _session_title
    if not title:
        for m in (history or []):
            if m.get("role") == "user":
                title = str(m.get("content"))[:20]
                break
    title = title or prev.get("title")
    data = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "title": title or "未命名会话",
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


def _session_summary(path=None):
    """读回会话 json；默认读 session_latest.json。"""
    p = Path(path) if path else SESSION_JSON
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------- M11-4：会话列表 ----------

def _archive_current_session():
    """把当前 session_latest.json 归档进 output/sessions/，返回归档路径（无记录则 None）。"""
    data = _session_summary()
    if not data:
        return None
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = SESSIONS_DIR / f"session_{stamp}.json"
        # 同名保护（1 秒内连开两个会话）
        n = 1
        while path.exists():
            path = SESSIONS_DIR / f"session_{stamp}_{n}.json"
            n += 1
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path
    except OSError as e:
        print(f"[session] 归档失败: {e}")
        return None


def _ensure_legacy_archived():
    """启动时把旧的 session_latest.json 并入会话列表（若尚未归档）。"""
    data = _session_summary()
    if not data or not SESSIONS_DIR.exists():
        return
    for p in SESSIONS_DIR.glob("session_*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("saved_at") == \
                    data.get("saved_at"):
                return   # 已归档过
        except (OSError, json.JSONDecodeError):
            continue
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        (SESSIONS_DIR / f"session_{stamp}_legacy.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _list_sessions():
    """列出所有会话（新→旧）：[(显示名, 文件路径, 说明)]。"""
    if not SESSIONS_DIR.exists():
        return []
    items = []
    for p in SESSIONS_DIR.glob("session_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        title = data.get("title") or "未命名会话"
        items.append((f"{title} · {data.get('saved_at', '')}", p, data))
    items.sort(key=lambda x: x[2].get("saved_at", ""), reverse=True)
    return items


def _sessions_choices():
    """会话列表的选项文案（新→旧）：标题 + 时间。"""
    return [name for name, _, _ in _list_sessions()]


def _session_list_update():
    """左栏会话列表（Radio）刷新：选中新→旧，且不选中任何项。"""
    choices = _sessions_choices()
    if not choices:
        return gr.update(choices=[], value=None,
                         label="📂 历史会话（暂无，点「➕ 新会话」开始）")
    return gr.update(choices=choices, value=None,
                     label=f"📂 历史会话（{len(choices)} 个，新的在前）")


def _sessions_dropdown_update():
    """兼容旧的下拉框入口（保留功能，不退化）。"""
    upd = _session_list_update()
    n = len(upd.get("choices") or [])
    return (gr.update(choices=upd.get("choices"),
                      value=upd.get("choices")[0] if n else None,
                      label=f"📂 会话（{n} 个，新的在前）"),
            f"共 {n} 个会话，可点击「↩️ 恢复所选会话」切换")


def _restore_from(data):
    """按会话 json 恢复：现有 load_slices 技能重放 + 读回虚拟切片。

    返回 (ok, message)；失败时给出原因，由调用方降级为新会话。
    """
    if not data:
        return False, "没有找到会话记录"
    args = data.get("load_args") or {}
    if not args:
        return False, "会话记录里没有 load_slices 参数，无法恢复"

    try:
        ok, msg = _agent.executor.run_step("load_slices", dict(args))
    except Exception as e:  # noqa: BLE001
        return False, f"重放 load_slices 失败: {type(e).__name__}: {e}"
    if not ok:
        return False, f"重放 load_slices 失败: {msg}"

    # 恢复 = 把该会话的虚拟层**重置**成存档里的那些，而不是接着现有的往后加。
    # 否则重复点「恢复会话」会层层累加（4 层 × 3 次 = 12 层），且面板显示的层数
    # 与会话文件里记录的对不上。顺带清掉 dense（它是基于旧虚拟层合并出来的）。
    _agent.session.virtual_slices.clear()
    _agent.session.dense = None

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
    return True, (f"已恢复会话「{data.get('title') or '未命名'}」："
                  f"{len(_agent.session.all_slices)} 张真实切片、"
                  f"{len(_agent.session.virtual_slices)} 层虚拟切片"
                  + (f"（其中 {n} 层从文件读回）" if n != len(_agent.session.virtual_slices) else ""))


def _restore_session():
    """恢复当前 session_latest.json（保留旧入口，供启动提示按钮使用）。"""
    data = _session_summary()
    if not data:
        return False, "没有找到会话文件 output/session_latest.json"
    global _session_title
    _session_title = data.get("title")
    return _restore_from(data)


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
            "在左侧「📂 会话」里可切换历史会话，或点【➕ 新会话】从零开始。")


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
    yield history, gr.update(), gr.update(), _panel_md()    # 记录本次计划里的 load_slices 参数，成功后写进会话文件供恢复用
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
    blank = (history, gr.update(), gr.update(), gr.update(), None,
             gr.update(interactive=False), gr.update(interactive=False))
    if not user_text:
        yield blank
        return

    history = list(history or [])
    history.append({"role": "user", "content": user_text})
    global _session_title
    if _session_title is None and user_text:
        _session_title = user_text[:20]      # M11-4：会话标题 = 首条指令前 20 字

    if _agent is None:
        history.append({"role": "assistant",
                        "content": f"❌ {_agent_error or 'Agent 不可用'}"})
        yield blank
        return

    history.append({"role": "assistant", "content": "🧠 正在规划…"})
    yield (history, gr.update(), gr.update(), _panel_md(), None,
           gr.update(interactive=False), gr.update(interactive=False))

    try:
        steps, reply, _dropped = _agent.prepare_steps(user_text)
    except Exception as e:  # noqa: BLE001
        history[-1] = {"role": "assistant", "content": f"❌ 规划失败: {e}"}
        yield (history, gr.update(), gr.update(), _panel_md(), None,
               gr.update(interactive=False), gr.update(interactive=False))
        return

    if not steps:
        history[-1] = {"role": "assistant",
                       "content": (reply or "❌ 没有可执行步骤，请换个说法或先加载数据")}
        yield (history, gr.update(), gr.update(), _panel_md(), None,
               gr.update(interactive=False), gr.update(interactive=False))
        return

    if skip_confirm:
        history[-1] = {"role": "assistant",
                       "content": _plan_card(steps, reply) + "  \n（已跳过确认）"}
        before = _collect_entries()
        before_top = before[0][0] if before else None
        for upd in _execute_steps(steps, history, before_top, current_pick):
            yield upd + (None, gr.update(interactive=False),
                         gr.update(interactive=False))
        return

    # 计划确认卡片：等用户点按钮
    history[-1] = {"role": "assistant", "content": _plan_card(steps, reply)}
    yield (history, gr.update(), gr.update(), _panel_md(), steps,
           gr.update(interactive=True), gr.update(interactive=True))


def do_confirm(history, current_pick, pending):
    """用户点【确认执行】。"""
    if not pending:
        yield (history, gr.update(), gr.update(), _panel_md(), None,
               gr.update(interactive=False), gr.update(interactive=False))
        return
    before = _collect_entries()
    before_top = before[0][0] if before else None
    for upd in _execute_steps(pending, list(history or []), before_top,
                              current_pick):
        yield upd + (None, gr.update(interactive=False),
                     gr.update(interactive=False))


def do_cancel(history, pending):
    """用户点【取消】：清空待确认计划。"""
    history = list(history or [])
    history.append({"role": "assistant", "content": "↩️ 已取消，等待下一条指令。"})
    return (history, gr.update(), gr.update(), _panel_md(), None,
            gr.update(interactive=False), gr.update(interactive=False))


def do_restore(history):
    """恢复当前 session_latest.json（顶部提示里的恢复入口）。"""
    history = list(history or [])
    history.append({"role": "assistant", "content": "💾 正在恢复上次会话…"})
    yield (history, gr.update(), gr.update(), _panel_md(), None,
           gr.update(interactive=False), gr.update(interactive=False))
    ok, msg = _restore_session()
    history.append({"role": "assistant",
                    "content": (f"✅ {msg}" if ok else
                                f"❌ 恢复失败：{msg}\n已切换为新会话。")})
    yield (history, gr.update(), gr.update(), _panel_md(), None,
           gr.update(interactive=False), gr.update(interactive=False))


def do_new_session(history):
    """M11-4：开始新会话 —— 先把当前记录归档进 output/sessions/，再重置会话状态。"""
    history = list(history or [])
    archived = _archive_current_session()
    global _session_title
    _session_title = None
    if _agent is not None:
        from agent.session import Session
        run_dir = OUTPUT_DIR / time.strftime("run_%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        _agent.session = Session()
        _agent.session.run_dir = run_dir
        _agent.executor.session = _agent.session   # executor 持有旧引用，必须同步
    msg = (f"🆕 已开始新会话（上一会话已归档：{archived.name}）"
           if archived else "🆕 已开始新会话")
    history.append({"role": "assistant", "content": msg})
    return (history, gr.update(), gr.update(), _panel_md(), None,
            gr.update(interactive=False), gr.update(interactive=False),
            _session_list_update())


def do_restore_selected(history, session_choice):
    """M11-4：恢复会话列表里选中的那个会话。"""
    history = list(history or [])
    target = None
    for name, path, _data in _list_sessions():
        if name == session_choice or f"{name} — " in str(session_choice):
            target = path
            break
    if target is None:
        history.append({"role": "assistant", "content": "⚠️ 没有找到所选会话。"})
        yield (history, gr.update(), gr.update(), _panel_md(), None,
               gr.update(interactive=False), gr.update(interactive=False),
               _session_list_update())
        return

    data = _session_summary(target)
    history.append({"role": "assistant",
                    "content": f"💾 正在恢复会话「{data.get('title') or '未命名'}」…"})
    yield (history, gr.update(), gr.update(), _panel_md(), None,
           gr.update(interactive=False), gr.update(interactive=False),
           gr.update())
    ok, msg = _restore_from(data)
    global _session_title
    _session_title = data.get("title")
    if ok:   # 恢复成功后把它同步为当前会话
        try:
            SESSION_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        except OSError:
            pass
    history.append({"role": "assistant",
                    "content": (f"✅ {msg}" if ok else f"❌ 恢复失败：{msg}")})
    yield (history, gr.update(), gr.update(), _panel_md(), None,
           gr.update(interactive=False), gr.update(interactive=False),
           gr.update())


# ---------- M11-1 / M11-2 的界面回调 ----------

def save_llm_settings(api_key, base_url, model):
    """保存 LLM 配置：写 .env + 热切换；返回 (顶部状态条, 保存提示)。"""
    ok, msg = _apply_llm_config(api_key, base_url, model)
    return _header_html(), msg


def scan_extra_folder(folder_text):
    """M11-2：把用户给的文件夹追加进下拉框（默认 output/ 扫描结果保留）。"""
    text = (folder_text or "").strip().strip('"')
    if not text:
        return gr.update(), "⚠️ 请填写文件夹路径"
    folder = Path(text)
    try:
        found = _scan_extra_dir(folder)
    except (OSError, ValueError) as e:
        return gr.update(), f"❌ {e}"
    if folder not in _EXTRA_DIRS:
        _EXTRA_DIRS.append(folder)
    # gradio 的 file 路由只放行 allowed_paths 内的文件
    try:
        demo.allowed_paths.append(str(folder.resolve()))
    except AttributeError:
        pass
    if not found:
        return (_dropdown_update(),
                f"⚠️ 该文件夹下没有 .html：{folder}")
    return (_dropdown_update(),
            f"✅ 已加入 {len(found)} 个 html（来源：📁 {folder}），已追加到列表末尾")


def refresh_all(_=None):
    """刷新列表按钮：同时刷会话列表与图列表。"""
    return _dropdown_update()


# ---------- 界面 ----------

def _compact_css():
    """M12：三段式布局的紧凑样式 + 隐藏 Gradio 页脚。

    gradio 6 里 css 必须传给 launch()（Blocks 会警告已迁移）。
    页脚没有 show_footer 参数（gradio 6 已移除），只能用 CSS 隐藏 DOM。
    """
    return """
    /* 隐藏 Gradio 页脚（“设置 / Built with Gradio”），launch 不可再关 */
    footer, .footer, [class*="footer"], #footer { display: none !important; }
    /* 整体留白压缩 */
    .gradio-container { padding: 4px !important; max-width: 100% !important; }
    .block { padding: 4px !important; }
    .form, .wrap { gap: 4px !important; }
    h1, h2, h3 { margin: 2px 0 !important; }
    details { margin: 2px 0 !important; }
    /* 左栏会话列表：超过 10 个时内部滚动，不撑高页面 */
    #session_list { max-height: 190px; overflow-y: auto; }
    #session_list label { font-size: 12px; line-height: 1.5; }
    /* 左栏整体不换行、贴边 */
    #sidebar { gap: 6px !important; }
    #sidebar .form { gap: 6px !important; }
    /* 让聊天区吃满高度 */
    #chatbot { min-height: 380px; }
    """


def toggle_view(is_visible):
    """M12-3：右栏 3D 视图显示/隐藏。

    注意：不能把 Column 本身当事件输入（传进来是组件对象，真值为 True，
    取反会恒为 False 导致永远收不起来），所以可见性用 gr.State 里的布尔值记录。
    返回 (Column 的 visible 更新, 新的可见性状态)。
    """
    new_state = not bool(is_visible)
    return gr.update(visible=new_state), new_state


def build_app():
    notice = _startup_notice()
    init_chat = []
    if notice:
        init_chat.append({"role": "assistant", "content": notice})

    _sess = _sessions_choices()
    _sess_label = (f"📂 历史会话（{len(_sess)} 个，新的在前）" if _sess
                   else "📂 历史会话（暂无，点「➕ 新会话」开始）")

    with gr.Blocks(title="SpatialZ 3D 组织重构智能体", fill_height=True,
                   css=_compact_css()) as demo:
        header = gr.HTML(_header_html())
        with gr.Row():
            # ---------- 左栏：会话管理 + 设置 ----------
            with gr.Column(scale=1, min_width=180, elem_id="sidebar"):
                new_btn = gr.Button("➕ 新会话", variant="primary")
                session_pick = gr.Radio(
                    choices=_sess, value=None, label=_sess_label,
                    elem_id="session_list", interactive=True)
                with gr.Accordion("⚙️ LLM 设置", open=False):
                    _env = _read_env_file()
                    api_key_box = gr.Textbox(
                        label="API Key（掩码显示，不写入聊天区）",
                        value=_env.get("MY_LLM_API_KEY", ""), type="password")
                    base_url_box = gr.Textbox(
                        label="Base URL",
                        value=_env.get("MY_LLM_BASE_URL", ""))
                    model_box = gr.Textbox(
                        label="模型名", value=_env.get("MY_LLM_MODEL", ""))
                    save_llm_btn = gr.Button("💾 保存并生效", variant="primary",
                                             size="sm")
                    llm_msg = gr.Markdown("")
                with gr.Accordion("📊 切片信息", open=False):
                    panel = gr.Markdown(value=_panel_md())

            # ---------- 中栏：对话区 ----------
            with gr.Column(scale=5, min_width=360):
                with gr.Row():
                    toggle_btn = gr.Button("🗺️ 3D视图", size="sm", scale=1)
                    restore_btn = gr.Button("💾 恢复上次会话", size="sm", scale=1)
                chatbot = gr.Chatbot(
                    label="聊天记录", value=init_chat, min_height=400,
                    autoscroll=True, show_label=False)
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

            # ---------- 右栏：3D 视图（默认收起，点按钮展开） ----------
            with gr.Column(scale=2, min_width=280, visible=False) as view_col:
                with gr.Row():
                    folder_box = gr.Textbox(
                        label="手动加入历史图文件夹",
                        placeholder=r"例如 D:\某个目录",
                        scale=4)
                    scan_btn = gr.Button("📂 扫描", size="sm", scale=1)
                _initial = _collect_entries()
                _choices = [name for name, _, _ in _initial]
                picker = gr.Dropdown(
                    choices=_choices,
                    value=_choices[0] if _choices else None,
                    label=_label_with_count(len(_scan_html_files())),
                    allow_custom_value=False, interactive=True)
                with gr.Row():
                    refresh_btn = gr.Button("刷新列表", scale=1, size="sm")
                    scan_msg = gr.Markdown("", scale=5)
                view = gr.HTML(
                    value=_iframe_for(_choices[0] if _choices else None),
                    label="3D 视图")

        pending = gr.State(None)
        # 顺序必须与各 handler 的 yield 顺序一致：
        # (chatbot, view, picker, panel, pending, confirm_btn, cancel_btn)
        outputs = [chatbot, view, picker, panel, pending, confirm_btn, cancel_btn]
        # 新建/恢复会话的处理器多返回一个：左栏会话列表
        sess_outputs = outputs + [session_pick]

        send_btn.click(respond, inputs=[txt, chatbot, picker, skip, pending],
                       outputs=outputs)
        txt.submit(respond, inputs=[txt, chatbot, picker, skip, pending],
                   outputs=outputs)
        confirm_btn.click(do_confirm, inputs=[chatbot, picker, pending],
                          outputs=outputs)
        cancel_btn.click(do_cancel, inputs=[chatbot, pending], outputs=outputs)
        restore_btn.click(do_restore, inputs=[chatbot], outputs=outputs)
        new_btn.click(do_new_session, inputs=[chatbot], outputs=sess_outputs)
        # 左栏点历史会话 → 直接恢复
        session_pick.change(do_restore_selected, inputs=[chatbot, session_pick],
                            outputs=sess_outputs)
        # 下拉框选中 → 立即加载该图；刷新按钮 → 重新扫描
        picker.change(_iframe_for, inputs=[picker], outputs=[view])
        refresh_btn.click(_dropdown_update, inputs=[], outputs=[picker])
        scan_btn.click(scan_extra_folder, inputs=[folder_box],
                       outputs=[picker, scan_msg])
        save_llm_btn.click(save_llm_settings,
                           inputs=[api_key_box, base_url_box, model_box],
                           outputs=[header, llm_msg])
        # M12-3：3D 视图栏展开/收起（可见性状态存在 State 里）
        view_visible = gr.State(False)
        toggle_btn.click(toggle_view, inputs=[view_visible],
                         outputs=[view_col, view_visible])
        # 启动时把归档会话并入左栏列表
        demo.load(lambda: _session_list_update(), outputs=[session_pick])
    return demo


demo = build_app()

if __name__ == "__main__":
    # queue 保证长任务期间界面不卡死；allowed_paths 允许 iframe 读取 output/ 下的视图文件
    # gradio 6 已移除 show_footer 参数：页脚改由 css 隐藏（见 _compact_css）
    # css 同时挂在 Blocks 与 launch 上——desktop_app.py 复用本 demo 但自己调 launch
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        demo.queue().launch(allowed_paths=[str(OUTPUT_DIR)], footer_links=[],
                            css=_compact_css())
