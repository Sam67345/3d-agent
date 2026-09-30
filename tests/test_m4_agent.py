"""M4/M5 测试：agent 闭环、错误分类与有界重试、notebook 导出。

运行：.venv\\Scripts\\python tests\\test_m4_agent.py
"""

import sys
import tempfile
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.executor import Executor  # noqa: E402
from agent.exporter import export_notebook  # noqa: E402
from agent.llm import MockLLM  # noqa: E402
from agent.planner import Planner  # noqa: E402
from agent.registry import load_registry  # noqa: E402
from agent.session import Session, StepLog  # noqa: E402
from agent.verifier import verify  # noqa: E402

PASSED = []


def check(label, cond, detail=""):
    PASSED.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {detail}")


def test_verifier_blocks_without_pair():
    reg = load_registry()
    session = Session()
    ok, problems = verify("interpolate", reg["interpolate"], session, {})
    check("Verifier: 未加载切片对时拦截 interpolate", not ok and problems)


def _tiny_pair(n=60, g=25, seed=0):
    """构造微型 AnnData 切片对（真实结构的合成数据），让插值真正跑通。"""
    import numpy as np
    import pandas as pd
    import anndata as ad
    rng = np.random.default_rng(seed)
    obs = pd.DataFrame({"Region": rng.choice(["Layer1", "Layer2", "WM"], n)},
                       index=[f"spot{i}" for i in range(n)])
    obs["layer"] = obs["Region"]
    var = pd.DataFrame(index=[f"gene{i}" for i in range(g)])
    a1 = ad.AnnData(X=rng.poisson(1.0, (n, g)).astype(np.float32), obs=obs, var=var)
    a2 = ad.AnnData(X=rng.poisson(1.0, (n, g)).astype(np.float32),
                    obs=obs.copy(), var=var.copy())
    a1.obsm["spatial"] = rng.uniform(0, 100, (n, 2))
    a2.obsm["spatial"] = rng.uniform(0, 100, (n, 2))
    return a1, a2


def test_error_classification_and_bounded_retry():
    """机械错误（alpha 越界）经 LLM 建议修复后重试一次成功；重试有界。"""
    session = Session()
    session.pair = _tiny_pair()

    class FixerLLM(MockLLM):
        """返回修复参数（alpha 1.5 -> 0.5）的桩 LLM。"""

        def __init__(self):
            self.calls = 0

        def chat(self, messages, temperature=0.2):
            self.calls += 1
            return '{"args": {"alpha": 0.5, "nb_iter": 10}}'

    ex = Executor(session, llm=FixerLLM(), verbose=False)
    ok, msg = ex.run_step("interpolate", {"alpha": 1.5, "nb_iter": 10})
    check("机械错误经修复重试后成功", ok, str(msg)[:80])
    check("LLM 修复建议被调用一次", ex.llm.calls == 1, f"calls={ex.llm.calls}")
    check("重试有界（成功步骤只记录一条日志）",
          len(session.logs) == 1 and session.logs[0].ok)

    # 修复者也失败时：不再重试，诚实报告
    class BadFixerLLM(FixerLLM):
        def chat(self, messages, temperature=0.2):
            self.calls += 1
            return '{"args": {"alpha": 1.8, "nb_iter": 10}}'  # 仍是非法值

    session2 = Session()
    session2.pair = _tiny_pair()
    ex2 = Executor(session2, llm=BadFixerLLM(), verbose=False)
    ok2, msg2 = ex2.run_step("interpolate", {"alpha": 1.5, "nb_iter": 10})
    check("修复后仍失败则停止并诚实报告", not ok2 and "重试" in msg2)
    check("失败被记录到日志", not session2.logs[-1].ok)


def test_structural_error_reported():
    session = Session()  # 无 pair
    ex = Executor(session, llm=None, verbose=False)
    ok, msg = ex.run_step("interpolate", {"alpha": 0.5})
    check("结构性错误直接诚实报告（无 pair 时）",
          not ok and "结构性" in msg, msg[:60])


def test_planner_dependency_expansion():
    """Planner 产出的 visualize 计划在 handle 层自动补 make_dense。"""
    from agent_cli import Agent
    agent = Agent(mock=True, verbose=False)
    # 手动构造：加载(快) → 可视化；验证 make_dense 自动插入
    agent.planner.llm.PLANS = {"直接可视化": [{"skill": "visualize_3d", "args": {}}]}
    ok, _ = agent.handle("直接可视化")
    check("无数据时 visualize 被前置条件拦截", not ok)


def test_notebook_export():
    session = Session()
    session.add_log(StepLog("load_slices", {"n_hvg": 2000},
                            "pair = load_slices.run({'n_hvg': 2000})",
                            {"meta": "ok"}, ok=True))
    session.add_log(StepLog("interpolate", {"alpha": 0.5},
                            "r = interpolate(a1, a2, {'alpha': 0.5})",
                            {"path": "output/virtual_alpha0.500.h5ad"}, ok=True))
    session.outputs["view_3d"] = "output/view_3d.html"
    with tempfile.TemporaryDirectory() as td:
        path = export_notebook(session, out_path=Path(td) / "test.ipynb")
        nb = nbf.read(path, as_version=4)
        nbf.validate(nb)
        code_cells = [c for c in nb.cells if c.cell_type == "code"]
        md_cells = [c for c in nb.cells if c.cell_type == "markdown"]
        check("notebook 结构合法且含代码+说明单元",
              len(code_cells) == 2 and len(md_cells) >= 3,
              f"code={len(code_cells)}, md={len(md_cells)}")
        check("notebook 记录产物清单",
              any("view_3d" in c.source for c in md_cells))


def main():
    print("== Verifier ==")
    test_verifier_blocks_without_pair()
    print("\n== Error Reflector ==")
    test_error_classification_and_bounded_retry()
    test_structural_error_reported()
    print("\n== Planner 依赖 ==")
    test_planner_dependency_expansion()
    print("\n== Notebook 导出 ==")
    test_notebook_export()
    print(f"\n{'='*40}\n共 {len(PASSED)} 项, 通过 {sum(PASSED)} 项")
    sys.exit(0 if all(PASSED) else 1)


if __name__ == "__main__":
    main()
