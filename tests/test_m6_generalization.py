"""M6 后端泛化收尾验收（4 项）。

运行：.venv\\Scripts\\python tests\\test_m6_generalization.py
注意：M6.3 端到端（5 张 MERFISH × 每对 2 层 = 8 层虚拟切片）约 3-8 分钟（nb_iter=200）；
M6.2 的 DLPFC 用 n_hvg=8000 使面板 > 5000 触发守卫（默认 n_hvg=2000 的 3436 基因面板不触发）。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills"))

from agent.executor import Executor  # noqa: E402
from agent.session import Session  # noqa: E402

MERFISH_DIR = ROOT / "data" / "merfish_five"
MERFISH_5 = [str(MERFISH_DIR / f"merfish_{i}_paste.h5ad") for i in (4, 9, 14, 19, 24)]

PASSED = []


def check(label, cond, detail=""):
    PASSED.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {detail}")


def _tiny_pair(n=40, g=25, seed=0):
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


# ---------------- M6.1 小基因面板自适应 ----------------

def test_m61_small_panel():
    from load_slices import run
    r = run(file_paths=[MERFISH_5[0], MERFISH_5[1]])  # 不传 n_hvg，默认 2000 > 155
    check("M6.1 MERFISH 2 张加载成功", len(r["adatas"]) == 2)
    check("M6.1 hvg_skipped=true", all(m["hvg_skipped"] for m in r["meta"]),
          str([m["hvg_skipped"] for m in r["meta"]]))
    check("M6.1 全基因面板 155", all(a.n_vars == 155 for a in r["adatas"]),
          str([a.n_vars for a in r["adatas"]]))
    import numpy as np
    check("M6.1 dense float32",
          all(a.X.dtype == np.float32 and not hasattr(a.X, "todense") for a in r["adatas"]))
    # N=5 也通（M6.3 前半）
    r5 = run(file_paths=MERFISH_5)
    ids = [m["sample_id"] for m in r5["meta"]]
    check("M6.3 load_slices N=5 按序加载",
          len(r5["adatas"]) == 5 and ids == [Path(p).stem for p in MERFISH_5],
          str(ids))


# ---------------- M6.2 default 模式守卫 ----------------

def test_m62_guard():
    from interpolate import run, default_mode_guard
    a1, a2 = _tiny_pair(g=6000, seed=5)  # 6000 > 5000，触发守卫
    r = run(a1, a2, syn_mode="default", nb_iter=5)
    check("M6.2 大面板 default 被守卫拦截", r.get("mode") == "guard", str(r.get("mode")))
    check("M6.2 警告含确认指引", r.get("warning") and "confirm_default=true" in r["warning"])
    # 放行语义在守卫层验证：confirm_default=True 时守卫返回 None（不拦截）。
    # 注意：default 模式的实际执行受 MENDER Windows 多进程问题影响（README M2.5 遗留），
    # 不在 M6 范围；此处只验证守卫不拦。
    check("M6.2 confirm_default=true 守卫放行",
          default_mode_guard(6000, "default", True) is None)
    check("M6.2 大面板 fast 不受守卫影响",
          default_mode_guard(6000, "fast", False) is None)
    r3 = run(a1, a2, syn_mode="fast", nb_iter=5)
    check("M6.2 fast 正常生成", r3.get("mode") == "single")


def test_m62_dlpfc_guard_e2e():
    """DLPFC 用 n_hvg=8000（面板 ~8-9k > 5000）走 executor 被守卫拦截。"""
    from load_slices import run as load
    pair = load(sample_ids=["151673", "151674"], n_hvg=8000)
    check("M6.2 DLPFC 面板 > 5000", pair["adata1"].n_vars > 5000,
          f"{pair['adata1'].n_vars} genes")
    session = Session()
    session.slices = list(pair["adatas"])
    session.pair = (pair["adata1"], pair["adata2"])
    session.pair_meta = pair["meta"]
    ex = Executor(session, llm=None, verbose=False)
    ok, msg = ex.run_step("interpolate", {"syn_mode": "default", "nb_iter": 10})
    check("M6.2 DLPFC + default 被警告拦截", ok and "confirm_default=true" in str(msg),
          str(msg)[:120])
    check("M6.2 拦截后无虚拟切片", len(session.virtual_slices) == 0)


def test_m62_merfish_default_allowed():
    """MERFISH 155 基因 + default：守卫放行（default 是小面板设计模式）。

    实际执行 default 模式会触发 MENDER 的 Windows 多进程句柄上限问题
    （README M2.5 遗留，与 M6 无关），故此处只验证守卫层放行语义。
    """
    from interpolate import default_mode_guard
    check("M6.2 MERFISH 155 基因 + default 守卫放行",
          default_mode_guard(155, "default", False) is None)


# ---------------- M6.3 多切片端到端 ----------------

def test_m63_multislice_e2e():
    session = Session()
    ex = Executor(session, llm=None, verbose=False)
    ok1, msg1 = ex.run_step("load_slices", {"file_paths": MERFISH_5})
    check("M6.3 加载 5 张 MERFISH", ok1, str(msg1)[:100])
    check("M6.3 session.slices=5", len(session.slices) == 5)

    t0 = time.time()
    # nb_iter=10：仅验证链路（OT 未收敛）；生产用默认 1000。8 层约 3 分钟。
    ok2, msg2 = ex.run_step("interpolate_multiple", {"n_layers": 2, "nb_iter": 10})
    check("M6.3 每对插 2 层执行成功", ok2, str(msg2)[:150])
    check("M6.3 产出 8 个虚拟层", len(session.virtual_slices) == 8,
          f"{len(session.virtual_slices)} layers, {time.time()-t0:.0f}s")
    # 逐对 alpha 校验：slice_id 形如 merfish_4-merfish_9-alpha0.333
    sids = [v.obs["slice_id"].iloc[0] for v in session.virtual_slices]
    expect_pairs = [p for p in ["merfish_4_paste-merfish_9_paste", "merfish_9_paste-merfish_14_paste",
                                "merfish_14_paste-merfish_19_paste", "merfish_19_paste-merfish_24_paste"]
                    for _ in range(2)]
    got_pairs = [s.rsplit("-alpha", 1)[0] for s in sids]
    check("M6.3 层序按对排列", got_pairs == expect_pairs, str(got_pairs))
    got_alphas = [round(float(s.rsplit("alpha", 1)[1]), 3) for s in sids]
    check("M6.3 每对 alpha=0.333/0.667",
          got_alphas == [round(1 / 3, 3), round(2 / 3, 3)] * 4, str(got_alphas))

    ok3, msg3 = ex.run_step("make_dense", {})
    check("M6.3 make_dense 成功", ok3, str(msg3)[:100])
    n_real = sum(m["n_spots"] for m in session.pair_meta)
    check("M6.3 dense 总层数 13",
          session.dense.obs["slice_id"].nunique() == 13,
          f"{session.dense.obs['slice_id'].nunique()} layers, "
          f"{session.dense.n_obs} spots（真实 {n_real} + 虚拟 8 层）")
    check("M6.3 dense 文件已保存",
          Path(session.outputs["dense"]).exists(), session.outputs.get("dense", ""))


# ---------------- M6.4 会话级输出隔离 ----------------

def test_m64_run_dir_isolation():
    dir1 = ROOT / "output" / time.strftime("run_%Y%m%d_%H%M%S")
    time.sleep(1.2)
    dir2 = ROOT / "output" / time.strftime("run_%Y%m%d_%H%M%S")
    check("M6.4 两个时间戳目录不同", dir1 != dir2)
    dir1.mkdir(parents=True, exist_ok=True)
    dir2.mkdir(parents=True, exist_ok=True)

    for d, seed in ((dir1, 7), (dir2, 8)):
        session = Session()
        session.run_dir = d
        a1, a2 = _tiny_pair(seed=seed)
        session.slices = [a1, a2]
        session.pair = (a1, a2)
        session.pair_meta = [{"sample_id": f"s{seed}1"}, {"sample_id": f"s{seed}2"}]
        ex = Executor(session, llm=None, verbose=False)
        ok, _ = ex.run_step("interpolate", {"nb_iter": 5})
        check(f"M6.4 会话输出落入 {d.name}", ok and list(d.glob('virtual_alpha*.h5ad')),
              str([p.name for p in d.glob('virtual_alpha*.h5ad')]))
        ex.run_step("make_dense", {})
        check("M6.4 dense 落入 run 目录",
              Path(session.outputs["dense"]).parent == d, session.outputs["dense"])
        ex.run_step("export_notebook", {})
        check("M6.4 notebook 落入 run 目录",
              Path(session.outputs["notebook"]).parent == d,
              session.outputs["notebook"])
    # 互不覆盖
    files1 = {p.name for p in dir1.iterdir()}
    files2 = {p.name for p in dir2.iterdir()}
    check("M6.4 两目录产物并存", files1 and files2, f"{files1} vs {files2}")


if __name__ == "__main__":
    test_m61_small_panel()
    test_m62_guard()
    test_m62_dlpfc_guard_e2e()
    test_m62_merfish_default_allowed()
    test_m63_multislice_e2e()
    test_m64_run_dir_isolation()
    print(f"\n== M6 验收结果: {sum(PASSED)}/{len(PASSED)} 通过 ==")
    if not all(PASSED):
        raise SystemExit(1)
