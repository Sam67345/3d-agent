"""interpolate 易用性改造验收（alpha 可选默认 0.5 + n_layers 多层模式）。

端到端走 Executor + Session（与 M4 同款微型切片对，nb_iter=10 仅验证链路）。

运行：.venv\\Scripts\\python tests\\test_interpolate_ease.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.executor import Executor  # noqa: E402
from agent.session import Session  # noqa: E402

PASSED = []


def check(label, cond, detail=""):
    PASSED.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {detail}")


def _tiny_pair(n=60, g=25, seed=0):
    """微型 AnnData 切片对（与 test_m4_agent 同款构造）。"""
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


def test_default_single_layer():
    """验收 1：run(a1, a2) 不传 alpha/n_layers → alpha=0.5 单层。"""
    session = Session()
    session.pair = _tiny_pair()
    ex = Executor(session, llm=None, verbose=False)
    ok, msg = ex.run_step("interpolate", {"nb_iter": 10})
    check("默认调用成功", ok, str(msg))
    check("默认生成 1 层", len(session.virtual_slices) == 1,
          f"virtual_slices={len(session.virtual_slices)}")
    if ok:
        sim = session.virtual_slices[0]
        check("默认 alpha=0.5", sim.obs["slice_id"].iloc[0] == "virtual_alpha0.500",
              sim.obs["slice_id"].iloc[0])
        check("输出文件已登记", any("virtual_alpha0.5" in k for k in session.outputs),
              str(list(session.outputs)))
        check("返回路径存在",
              Path(session.outputs["virtual_alpha0.5"]).exists(),
              session.outputs.get("virtual_alpha0.5", ""))


def test_n_layers_multi():
    """验收 2：n_layers=3 → alpha 0.25/0.5/0.75 三层，session.virtual_slices 长度为 3。"""
    session = Session()
    session.pair = _tiny_pair(seed=1)
    ex = Executor(session, llm=None, verbose=False)
    ok, msg = ex.run_step("interpolate", {"n_layers": 3, "nb_iter": 10})
    check("多层调用成功", ok, str(msg))
    check("生成 3 层", len(session.virtual_slices) == 3,
          f"virtual_slices={len(session.virtual_slices)}")
    sids = [v.obs["slice_id"].iloc[0] for v in session.virtual_slices]
    check("alpha 序列 0.25/0.5/0.75（升序）",
          sids == ["virtual_alpha0.250", "virtual_alpha0.500", "virtual_alpha0.750"],
          str(sids))
    for a in ("0.25", "0.5", "0.75"):
        check(f"输出文件登记 virtual_alpha{a}",
              f"virtual_alpha{a}" in session.outputs and
              Path(session.outputs[f"virtual_alpha{a}"]).exists(),
              session.outputs.get(f"virtual_alpha{a}", ""))


def test_invalid_alpha_rejected():
    """验收 3：alpha=2.0 仍然被拒绝。"""
    session = Session()
    session.pair = _tiny_pair(seed=2)
    ex = Executor(session, llm=None, verbose=False)
    ok, msg = ex.run_step("interpolate", {"alpha": 2.0, "nb_iter": 10})
    check("alpha=2.0 被拒绝", not ok, str(msg))
    check("拒绝后无虚拟切片", len(session.virtual_slices) == 0)


def test_alpha_takes_precedence_over_n_layers():
    """附加：alpha 与 n_layers 同给时 alpha 优先，并注明。"""
    session = Session()
    session.pair = _tiny_pair(seed=3)
    ex = Executor(session, llm=None, verbose=False)
    ok, msg = ex.run_step("interpolate",
                          {"alpha": 0.3, "n_layers": 5, "nb_iter": 10})
    check("alpha 优先调用成功", ok, str(msg))
    check("只生成 1 层", len(session.virtual_slices) == 1)
    check("结果是 alpha=0.3",
          session.virtual_slices[0].obs["slice_id"].iloc[0] == "virtual_alpha0.300")
    check("结果注明 n_layers 被忽略", "note" in str(msg) or "忽略" in str(msg),
          str(msg))


if __name__ == "__main__":
    test_default_single_layer()
    test_n_layers_multi()
    test_invalid_alpha_rejected()
    test_alpha_takes_precedence_over_n_layers()
    print(f"\n== 验收结果: {sum(PASSED)}/{len(PASSED)} 通过 ==")
    if not all(PASSED):
        raise SystemExit(1)
