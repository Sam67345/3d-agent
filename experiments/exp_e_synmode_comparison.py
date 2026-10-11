"""任务 E：syn_mode 模式对比（fast vs default），用于解释与论文数值的差距。

发现：任务 C 复现论文 Fig.2e 时，我们得到的 Moran's I Spearman R ≈ 0.92~0.94，
略低于论文的 0.96~0.98。一个可检验的原因假设是——

  SpatialZ 源码 `Generate_spatialz` 的默认参数是 `syn_mode='default'`（MENDER 微环境
  + 逐基因抽样），而本项目 skills 全线使用 `syn_mode='fast'`（10-NN 同类型均值）。
  若假设成立，default 模式的 R 应高于 fast，接近论文数值。

注意：这不是"调参硬凑论文数值"，而是把协议差异作为一个自变量单独测量并如实记录。
本实验只跑第 2 节（一个 holdout），两种模式同参数（alpha=0.5, nb_iter=1000, seed=42）。
STARmap 只有 28 个基因，远低于 default 模式的 5000 基因上限，可直接运行。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Timer, add_spatialz_to_path, gearys_c_gene_wise,  # noqa: E402
                    morans_i_gene_wise, save_json, set_seed,
                    shim_anndata_concatenate, spearman_r)

STARMAP = Path(__file__).resolve().parent.parent / "data" / "starmap" / \
    "STARmap_Wang2018three_data_3D_data.h5ad"
Z_KEEP = (13.0, 91.0)
N_SECTIONS = 7
ALPHA = 0.5
NB_ITER = 1000
CELL_TYPE_KEY = "leiden"


def _limit_mender_processes(n_proc: int = 4) -> str:
    """把 MENDER 的多进程数限制到 n_proc（运行时补丁，不改任何源码文件）。

    背景：SpatialZ 的 default 模式会调用 `MENDER.MENDER.run_representation_mp(200)`，
    官方默认开 **200 个进程**。在 Windows 上每个 spawn 进程都要重新导入一遍依赖，
    直接把页面文件撑爆（实测 WinError 1455「页面文件太小」）。
    本项目数据只有 3 个 batch（MENDER 的 mp 是对 batch 维度并行的），
    开 4 个进程足够，且不改变算法本身的计算逻辑。
    """
    import MENDER

    orig = MENDER.MENDER.run_representation_mp

    def patched(self, mp=n_proc, group_norm=False):
        print(f"[runtime patch] MENDER 进程数 {mp} -> {n_proc}"
              f"（官方默认 200，本机页面文件不足）", flush=True)
        return orig(self, n_proc, group_norm)

    MENDER.MENDER.run_representation_mp = patched
    return "MENDER.run_representation_mp"


def main() -> int:
    t = Timer("任务E：fast vs default 模式对比")
    set_seed(42)
    add_spatialz_to_path()
    import anndata as ad
    from SpatialZ import Generate_spatialz
    from scipy import sparse as sp

    patched = _limit_mender_processes(4)
    print(f"[1/5] 已打运行时补丁: {patched}；{shim_anndata_concatenate()}", flush=True)
    print("[2/5] 按论文协议分层（z 13~91，7 节）", flush=True)
    adata = ad.read_h5ad(STARMAP)
    z = np.asarray(adata.obs["z"], dtype=float)
    a = adata[(z >= Z_KEEP[0]) & (z <= Z_KEEP[1])].copy()
    zz = np.asarray(a.obs["z"], dtype=float)
    edges = np.linspace(zz.min(), zz.max() + 1e-6, N_SECTIONS + 1)
    secs = []
    for i in range(N_SECTIONS):
        m = (zz >= edges[i]) & (zz < edges[i + 1])
        s = a[m].copy()
        s.obsm["spatial"] = np.asarray(s.obs[["x", "y"]].values, dtype=float)
        secs.append(s)
    print(f"  各节细胞数: {[s.n_obs for s in secs]}", flush=True)

    def dense(x):
        return np.asarray(x.todense()) if sp.issparse(x) else np.asarray(x)

    up, dn, real = secs[0], secs[2], secs[1]      # 抽掉第 2 节
    X_real = dense(real.X)
    xy_real = np.asarray(real.obsm["spatial"])
    mi_real = morans_i_gene_wise(X_real, xy_real)
    gc_real = gearys_c_gene_wise(X_real, xy_real)

    out = {}
    for mode in ("fast", "default"):
        print(f"[3/5] syn_mode={mode}", flush=True)
        t0 = time.time()
        try:
            sim = Generate_spatialz(
                up.copy(), dn.copy(), adata1_id="s1", adata2_id="s3",
                alpha=ALPHA, device="cpu", nb_iter_max=NB_ITER, seed=42,
                cell_type_key=CELL_TYPE_KEY, syn_mode=mode, k_sam=50,
                add_obs_list=[CELL_TYPE_KEY], verbose=False)
        except Exception as e:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            print(f"  [失败] {type(e).__name__}: {e}", flush=True)
            out[mode] = {"status": "failed", "error": f"{type(e).__name__}: {e}",
                         "traceback_tail": tb.strip().splitlines()[-8:]}
            t.lap(f"{mode} 失败（已记录）")
            continue
        el = time.time() - t0
        mi = morans_i_gene_wise(dense(sim.X), np.asarray(sim.obsm["spatial"]))
        gc = gearys_c_gene_wise(dense(sim.X), np.asarray(sim.obsm["spatial"]))
        r_mi, p_mi = spearman_r(mi_real, mi)
        r_gc, p_gc = spearman_r(gc_real, gc)
        out[mode] = {
            "status": "ok",
            "n_virtual": int(sim.n_obs), "elapsed_sec": round(el, 1),
            "morans_i": {"spearman_r": r_mi, "p_value": p_mi},
            "gearys_c": {"spearman_r": r_gc, "p_value": p_gc},
            "morans_i_real_median": float(np.nanmedian(mi_real)),
            "morans_i_virtual_median": float(np.nanmedian(mi)),
        }
        print(f"  → Moran's I R={r_mi:.3f}(p={p_mi:.1e}) Geary's C R={r_gc:.3f} "
              f"| 耗时 {el:.0f}s | Moran-I 中位 真实 {np.nanmedian(mi_real):.3f} "
              f"虚拟 {np.nanmedian(mi):.3f}", flush=True)
        t.lap(f"{mode} 完成")

    print("[4/5] 汇总", flush=True)
    both_ok = all(v.get("status") == "ok" for v in out.values())
    delta = {}
    if both_ok:
        delta["morans_r_gain"] = (out["default"]["morans_i"]["spearman_r"]
                                  - out["fast"]["morans_i"]["spearman_r"])
        delta["gearys_r_gain"] = (out["default"]["gearys_c"]["spearman_r"]
                                  - out["fast"]["gearys_c"]["spearman_r"])
    payload = {
        "experiment": "E_SYNMODE_FAST_VS_DEFAULT",
        "runtime_patch": "MENDER.run_representation_mp 进程数 200->4（不改源码文件）",
        "deviation_note": "default 模式官方默认开 200 进程，本机 Windows 页面文件不足"
                          "（WinError 1455），故运行时限制为 4；算法逻辑未变",
        "purpose": "检验'我们 R 略低于论文'是否由 syn_mode 协议差异造成"
                   "（论文源码默认 default/MENDER，本项目技能全线 fast）",
        "seed": 42, "alpha": ALPHA, "nb_iter_max": NB_ITER,
        "holdout": "第 2 节（用第 1+3 节推断）",
        "paper_value_fig2e": {"slice2_morans_r": 0.97, "slice2_gearys_r": 0.97},
        "ours": out,
        "default_minus_fast": delta,
        "runtime_sec": round(t.total(), 1),
    }
    save_json("synmode_comparison.json", payload)

    print("\n==== 任务E 结论 ====")
    print(f"  fast    : Moran's I R={out['fast']['morans_i']['spearman_r']:.3f}  "
          f"Geary's C R={out['fast']['gearys_c']['spearman_r']:.3f}  "
          f"耗时 {out['fast']['elapsed_sec']}s")
    if out.get("default", {}).get("status") == "ok":
        print(f"  default : Moran's I R={out['default']['morans_i']['spearman_r']:.3f}  "
              f"Geary's C R={out['default']['gearys_c']['spearman_r']:.3f}  "
              f"耗时 {out['default']['elapsed_sec']}s")
    else:
        print(f"  default : 运行失败 -> {out.get('default', {}).get('error')}")
        print("  结论：default(MENDER) 模式在本机不可用，无法检验该假设")
    print("  论文值  : Moran's I R=0.97  Geary's C R=0.97")
    print(f"  总耗时 {t.total():.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())