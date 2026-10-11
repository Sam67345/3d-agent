"""桌面端内核：Agent 初始化 + 会话/面板/图扫描/LLM 配置等共享逻辑。

M16 新增。背景：此前 api_server.py 直接 `import web_app as W`复用这22 个符号，
而 web_app.py 是 Gradio 版网页内核（含 gradio 依赖、1000+ 行 UI 接线），
CLI 版 agent_cli.py 一旦归档就会断链。本模块把**与UI 形态无关**的部分抽出来：

  · 路径常量与 Agent 初始化（含 LLM 配置热切换）
  · 会话：保存 / 读取 / 归档 / 列表 / 恢复 / 重命名 / AI 命名
  · 面板 markdown、计划卡片、validate 结果解读
  · 历史3D 图扫描（output/ 一级子目录 + 手动追加目录）

Gradio 版专属（gr.* 接线、事件回调、desktop_app 的 demo）已全部剔除。
React 桌面端走 api_server -> core_bootstrap，不再依赖任何被归档的文件。
"""

import html as html_mod
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "output"
SESSION_JSON = OUTPUT_DIR / "session_latest.json"
SESSIONS_DIR = OUTPUT_DIR / "sessions"
ENV_FILE = ROOT / ".env"

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
_ALPHA_RE = re.compile(r"alpha([0-9]*\.?[0-9]+)")

# 当前会话标题（AI 自动命名或手动重命名后写入）
_session_title = None

# M11-3：右栏视图的可用显示区（1400×900 窗口下的右栏约 660 宽）
VIEW_MAX_W = 660
VIEW_MAX_H = 470

PLACEHOLDER = (
    '<div style="color:#888;padding:24px;text-align:center;">'
    '还没有 3D 图。执行「画 3D 图」，或从上方下拉框选择历史生成的 HTML</div>'
)

from agent.core import Agent, LLMConfigError
from agent.executor import Executor as _BaseExecutor
from agent.llm import LLMClient

# pythonw / GBK 控制台下 emoji 打印会抛 UnicodeEncodeError，统一兜住
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

# 手动追加的文件夹（默认扫描 output/ 之外的部分）
_EXTRA_DIRS = []
_CHOICE_MAP = {}


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
    _run_dir = OUTPUT_DIR / time.strftime("run_%Y%m%d_%H%M%S")
    _run_dir.mkdir(parents=True, exist_ok=True)
    _agent = Agent(mock=False, run_dir=_run_dir)
    _agent.executor = _UIExecutor(_agent.session, llm=_agent.llm, verbose=True)
except LLMConfigError as e:
    _agent_error = f"Agent 初始化失败：{e}"

class _UIExecutor(_BaseExecutor):
    """把 Executor 的 print 日志转给 UI（不改 agent/，用子类覆写 _emit）。"""

    sink = None

    def _emit(self, msg):
        if self.verbose:
            print(msg)
        if self.sink:
            self.sink(msg)
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
        # 完整对话历史（前端切换会话时要用它还原聊天区）；保留旧的 chat_summary 字段
        # 以兼容已有会话文件
        "messages": [
            {"role": str(m.get("role", "assistant")),
             "content": str(m.get("content", "")),
             "ts": str(m.get("ts", ""))}
            for m in (history or []) if m.get("content")],
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
_TITLE_MAX_LEN = 24
def _fallback_title(text: str) -> str:
    """没有 LLM 或 LLM 失败时的兜底标题：取首个指令的前若干字。"""
    t = " ".join(str(text or "").split())
    return t[:_TITLE_MAX_LEN] if t else "未命名会话"
def auto_title(user_text: str, max_len: int = _TITLE_MAX_LEN) -> str:
    """用 LLM 给会话起一个短标题（失败时退回截断）。

    设计要点：
    · 标题只描述"这次在做什么"，不描述文件路径（旧的截断式标题常出现
      `插入"C:\\Users\\...` 这种毫无信息量的内容）
    · LLM 不可用/超时/返回垃圾内容时，一律回退到截断，绝不让命名失败影响主流程
    """
    fallback = _fallback_title(user_text)
    if _agent is None or getattr(_agent, "llm", None) is None:
        return fallback
    try:
        prompt = (
            "给下面这次空间转录组数据分析起一个**简短标题**。\n"
            f"要求：不超过 {max_len} 个字；中文；只说这次在做什么"
            "（如「插值DLPFC切片」「画3D脑图」），不要出现文件路径、"
            "引号、句号或解释性文字；只输出标题本身。\n\n"
            f"用户指令：{str(user_text)[:300]}"
        )
        raw = _agent.llm.chat([{"role": "user", "content": prompt}],
                              temperature=0.2)
        text = str(raw or "").strip().splitlines()[0] if str(raw or "").strip() else ""
        text = text.strip().strip("「」\"'`。，,.!！?？:：")
        if not text or len(text) > max_len + 8:
            return fallback
        return text[:max_len]
    except Exception as e:  # noqa: BLE001
        print(f"[session] AI 命名失败，用兜底标题: {type(e).__name__}: {e}")
        return fallback
def rename_session_file(path, title: str) -> tuple[bool, str]:
    """重命名指定会话文件的 title 字段。返回 (ok, message)。"""
    p = Path(path)
    if not p.exists():
        return False, f"会话文件不存在：{p.name}"
    name = " ".join(str(title or "").split())
    if not name:
        return False, "标题不能为空"
    if len(name) > 40:
        return False, "标题过长（最多 40 字）"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, f"会话文件读取失败：{e}"
    data["title"] = name
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError as e:
        return False, f"写入失败：{e}"
    return True, f"已重命名为「{name}」"
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
