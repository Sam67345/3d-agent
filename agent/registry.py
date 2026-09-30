"""技能注册表：解析 skills/*.md 的 YAML 头，供 Planner 路由与 Verifier 检查。"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

# agent 内置技能（无独立 md 文件）：不直接做计算，操作 Session
BUILTIN_SKILLS = {
    "overview": {
        "name": "overview",
        "description": "查看当前已加载切片的数据概况（spot 数、基因数、坐标范围、分层构成）",
        "inputs": [],
        "preconditions": ["session.has_pair"],
    },
    "export_notebook": {
        "name": "export_notebook",
        "description": "把本次会话的全部执行步骤导出为可复现的 Jupyter notebook",
        "inputs": [],
        "preconditions": ["len(session.logs) > 0"],
    },
    "make_dense": {
        "name": "make_dense",
        "description": "把真实切片与全部虚拟切片合并成一个多层 AnnData（可视化/验证的前置）",
        "inputs": [],
        "preconditions": ["session.has_virtual"],
    },
}


def load_registry(skills_dir=None):
    """读取 skills/*.md 的 YAML front matter，返回 {name: meta}。"""
    d = Path(skills_dir) if skills_dir else SKILLS_DIR
    registry = dict(BUILTIN_SKILLS)
    for md in sorted(d.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("---", 3)
        meta = yaml.safe_load(text[3:end])
        if meta and meta.get("name"):
            registry[meta["name"]] = meta
    return registry


def routing_table(registry):
    """给 Planner 的精简路由表：name + description + 参数名清单。"""
    lines = []
    for name, meta in registry.items():
        desc = meta.get("description", "")
        params = []
        for inp in meta.get("inputs", []) or []:
            req = "" if inp.get("required") else "=可选"
            params.append(f"{inp.get('name')}{req}")
        param_str = f"（参数: {', '.join(params)}）" if params else "（无参数）"
        lines.append(f"- {name}: {desc} {param_str}")
    return "\n".join(lines)
