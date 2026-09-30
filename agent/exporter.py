"""M5 导出器：把 Session 的执行日志导出为可复现的 Jupyter notebook。"""

from pathlib import Path

import nbformat as nbf

from .registry import ROOT

HEADER = '''# SpatialZ 3D 插值会话记录（自动导出）

本 notebook 由 agent 导出，包含会话中每一步的可复现代码。
运行前请确保：项目虚拟环境已激活（.venv），且工作目录为项目根目录。
'''


def export_notebook(session, out_path=None):
    """把 session.logs 导出为 .ipynb，返回文件路径。"""
    nb = nbf.v4.new_notebook()
    cells = [nbf.v4.new_markdown_cell(HEADER)]

    for i, log in enumerate(session.logs, 1):
        status = "✅ 成功" if log.ok else f"❌ 失败: {log.error}"
        md = (f"## 步骤 {i}: `{log.skill}`\n\n"
              f"- 时间: {log.time}\n- 参数: `{log.args_summary}`\n"
              f"- 结果: {status}\n\n"
              f"```\n{log.result_summary}\n```")
        cells.append(nbf.v4.new_markdown_cell(md))
        cells.append(nbf.v4.new_code_cell(
            f"# 步骤 {i}: {log.skill}\n{log.code}"))

    # 状态收尾 cell：可视化产物
    if session.outputs:
        lines = "\n".join(f"- {k}: {v}" for k, v in session.outputs.items())
        cells.append(nbf.v4.new_markdown_cell(f"## 会话产物\n\n{lines}"))

    nb.cells = cells
    nb.metadata.kernelspec = {"display_name": "Python 3", "language": "python",
                              "name": "python3"}

    out = Path(out_path) if out_path else ROOT / "output" / "reproducible.ipynb"
    out.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(nb, out)
    return str(out)
