"""任务 C：按 SpatialZ 论文协议复现 STARmap 基准（Fig. 2）。

与 M10 任务 A 的差别（很重要）：
  M10 任务 A：z 全程 6~94 直接切 7 层，把第 2~6 层逐层抽掉（5 次 holdout）
  本任务 C（论文口径，Methods 原文）：
    · 剔除最上（z 6~13）与最下（z 91~94）层，"to avoid technical noise"
    · 把剩余组织切成 7 个连续切片
    · **移除第 2、4、6 节** → 稀疏堆叠（SSS）= 真实 1/3/5/7
    · 由 1/3/5/7 推断缺失的第 2/4/6 节（虚拟层）
  只有按这个协议做，才能和论文 Fig.2 的数值直接对话。

复现的论文声明（数值从论文 Fig.2 图中读取，见 VALIDATION2.md）：
  Fig.2e 虚拟层 vs 对应真实层：Moran's I R=0.96~0.98；Geary's C R=0.95~0.97
  Fig.2g 稀疏堆叠(SSS) vs 真实3D：Moran's I R=0.98，Geary's C R=0.98
  Fig.2h SpatialZ 稠密体 vs 真实3D：Moran's I R=0.99，Geary's C R=0.99
  Fig.2d 八项统计指标的 real vs virtual 分布对比
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Timer, add_spatialz_to_path, ensure_font_cn,  # noqa: E402
                    gearys_c_gene_wise, morans_i_gene_wise, save_json,
                    save_png, set_seed, spearman_r)

STARMAP = Path(__file__).resolve().parent.parent / "data" / "starmap" / \
    "STARmap_Wang2018three_data_3D_data.h5ad"
Z_KEEP = (13.0, 91.0)        # 论文剔除 z 6~13 与 91~94
N_SECTIONS = 7
HOLD_OUT = (2, 4, 6)         # 论文移除第 2/4/6 节（1-based）
ALPHA = 0.5
NB_ITER = 1000
CELL_TYPE_KEY = "leiden"


def build_sections(adata):
    """按论文协议：先剔除 z 边缘层，再等宽切 7 节。返回 (list[AnnData], 7 层的 z 边界)。"""
    import anndata as ad
    z = np.asarray(adata.obs["z"], dtype=float)
    keep = (z >= Z_KEEP[0]) & (z <= Z_KEEP[1])
    a = adata[keep].copy()
    zz = np.asarray(a.obs["z"], dtype=float)
    edges = np.linspace(zz.min(), zz.max() + 1e-6, N_SECTIONS + 1)
    sections = []
    for i in range(N_SECTIONS):
        m = (zz >= edges[i]) & (zz < edges[i + 1])
        s = a[m].copy()
        s.obsm["spatial"] = np.asarray(s.obs[["x", "y"]].values, dtype=float)
        s.obsm["spatial3d"] = np.asarray(s.obs[["x", "y", "z"]].values, dtype=float)
        sections.append(s)
        print(f"  第{i + 1} 节: z[{edges[i]:.1f},{edges[i + 1]:.1f}) -> {s.n_obs} 细胞",
              flush=True)
    return sections, edges


def eight_metrics(expr: np.ndarray):
    """论文 Fig.2d 的八项统计指标，逐细胞/逐基因取值。

    (1) 基因 log 表达均值      (2) 基因 log 表达方差
    (3) 基因检出频率            (4) 基因-基因相关（两两）
    (5) 细胞 log 文库大小      (6) 细胞-细胞距离（50 维 PCA 空间）
    (7) 细胞检出频率            (8) 细胞-细胞相关（两两）
    """
    from sklearn.decomposition import PCA
    logX = np.log1p(expr)
    out = {}
    out["1_mean_log_expr_per_gene"] = logX.mean(axis=0)
    out["2_var_log_expr_per_gene"] = logX.var(axis=0, ddof=1)
    out["3_gene_detection_freq"] = (expr > 0).mean(axis=0)
    # (4) 基因对相关：取上三角平均（n_gene=28，全对 378 个）
    gm = logX.mean(axis=0)
    num = ((logX - gm) ** 2).sum(axis=0)
    cc = (logX.T @ logX) / logX.shape[0] - np.outer(gm, gm)
    denom = np.sqrt(np.outer(num, num))
    rmat = np.where(denom > 0, cc / denom, np.nan)
    iu = np.triu_indices_from(rmat, k=1)
    out["4_gene_gene_corr"] = rmat[iu]
    out["5_cell_log_lib_size"] = np.log1p(expr.sum(axis=1))
    # (6) 细胞-细胞距离（50 维 PCA）
    n_comp = min(50, min(logX.shape) - 1)
    pcs = PCA(n_components=n_comp, random_state=42).fit_transform(logX)
    from scipy.spatial.distance import pdist
    out["6_cell_cell_distance"] = pdist(pcs[:, :50])[:20000]
    out["7_cell_detection_freq"] = (expr > 0).mean(axis=1)
    # (8) 细胞对相关：随机取 2000 对（seed 固定）
    rng = np.random.default_rng(42)
    n = logX.shape[0]
    ii = rng.integers(0, n, 20000)
    jj = rng.integers(0, n, 20000)
    keep = ii != jj
    ii, jj = ii[keep][:2000], jj[keep][:2000]
    a_, b_ = logX[ii], logX[jj]
    a_ = a_ - a_.mean(axis=1, keepdims=True)
    b_ = b_ - b_.mean(axis=1, keepdims=True)
    den = np.sqrt((a_ ** 2).sum(axis=1) * (b_ ** 2).sum(axis=1))
    out["8_cell_cell_corr"] = np.where(den > 0, (a_ * b_).sum(axis=1) / den, np.nan)
    return out


def main() -> int:
    t = Timer("任务C：STARmap 论文协议复现")
    set_seed(42)
    add_spatialz_to_path()
    import anndata as ad
    from SpatialZ import Generate_spatialz
    from scipy import sparse as sp
    from scipy.stats import ks_2samp

    print("[1/6] 读取 + 按论文协议分层", flush=True)
    adata = ad.read_h5ad(STARMAP)
    sections, edges = build_sections(adata)
    t.lap("分层完成")

    def dense(x):
        return np.asarray(x.todense()) if sp.issparse(x) else np.asarray(x)

    results = {}
    virtual_sections = {}
    for idx in HOLD_OUT:
        i = idx - 1
        up, dn = sections[i - 1], sections[i + 1]
        cache = Path(__file__).resolve().parent.parent / "output" / "validation" / \
            "_cache_exp_c"
        cpath = cache / f"virtual_section_{idx}.h5ad"
        if cpath.exists():
            print(f"[2/6] 第 {idx} 节命中缓存，跳过插值", flush=True)
            sim = ad.read_h5ad(cpath)
            up, dn = sections[i - 1], sections[i + 1]
            virtual_sections[idx] = sim
            real = sections[i]
            X_r = dense(real.X)
            X_s = dense(sim.X)
            mi_r = morans_i_gene_wise(X_r, np.asarray(real.obsm["spatial"]))
            mi_s = morans_i_gene_wise(X_s, np.asarray(sim.obsm["spatial"]))
            gc_r = gearys_c_gene_wise(X_r, np.asarray(real.obsm["spatial"]))
            gc_s = gearys_c_gene_wise(X_s, np.asarray(sim.obsm["spatial"]))
            r_mi, p_mi = spearman_r(mi_r, mi_s)
            r_gc, p_gc = spearman_r(gc_r, gc_s)
            m_real = eight_metrics(X_r); m_syn = eight_metrics(X_s)
            eight = {}
            for k in m_real:
                vr, vs = m_real[k], m_syn[k]
                stat, p = ks_2samp(vr[~np.isnan(vr)], vs[~np.isnan(vs)])
                eight[k] = {"real_median": float(np.nanmedian(vr)),
                            "virtual_median": float(np.nanmedian(vs)),
                            "median_ratio": float(np.nanmedian(vs) / (np.nanmedian(vr) + 1e-12)),
                            "ks_stat": float(stat), "ks_p": float(p)}
            results[f"section_{idx}"] = {
                "n_real": int(real.n_obs), "n_virtual": int(sim.n_obs),
                "morans_i": {"spearman_r": r_mi, "p_value": p_mi},
                "gearys_c": {"spearman_r": r_gc, "p_value": p_gc},
                "eight_metrics": eight}
            print(f"  → Moran's I R={r_mi:.3f} (p={p_mi:.1e}) | "
                  f"Geary's C R={r_gc:.3f} (p={p_gc:.1e})  [缓存]", flush=True)
            t.lap(f"第 {idx} 节完成（缓存）")
            continue
        print(f"[2/6] 推断第 {idx} 节（用第 {idx-1} + {idx+1} 节）", flush=True)
        sim = Generate_spatialz(
            up.copy(), dn.copy(), adata1_id=f"s{idx-1}", adata2_id=f"s{idx+1}",
            alpha=ALPHA, device="cpu", nb_iter_max=NB_ITER, seed=42,
            cell_type_key=CELL_TYPE_KEY, syn_mode="fast", k_sam=50,
            add_obs_list=[CELL_TYPE_KEY], verbose=False)
        # 虚拟层没有真实 z，用上下两节 z 的中点代表它所处的深度
        z_up = float(np.asarray(up.obs["z"]).mean())
        z_dn = float(np.asarray(dn.obs["z"]).mean())
        sim.obsm["spatial3d"] = np.hstack(
            [np.asarray(sim.obsm["spatial"]), np.full((sim.n_obs, 1), (z_up + z_dn) / 2)])
        cache = Path(__file__).resolve().parent.parent / "output" / "validation" / \
            "_cache_exp_c"
        cache.mkdir(parents=True, exist_ok=True)
        cpath = cache / f"virtual_section_{idx}.h5ad"
        sim.write_h5ad(cpath)
        print(f"    [缓存] {cpath.name}", flush=True)
        virtual_sections[idx] = sim
        real = sections[i]
        mi_r = morans_i_gene_wise(dense(real.X), np.asarray(real.obsm["spatial"]))
        mi_s = morans_i_gene_wise(dense(sim.X), np.asarray(sim.obsm["spatial"]))
        gc_r = gearys_c_gene_wise(dense(real.X), np.asarray(real.obsm["spatial"]))
        gc_s = gearys_c_gene_wise(dense(sim.X), np.asarray(sim.obsm["spatial"]))
        r_mi, p_mi = spearman_r(mi_r, mi_s)
        r_gc, p_gc = spearman_r(gc_r, gc_s)
        # 八项指标
        m_real = eight_metrics(dense(real.X))
        m_syn = eight_metrics(dense(sim.X))
        eight = {}
        for k in m_real:
            vr, vs = m_real[k], m_syn[k]
            stat, p = ks_2samp(vr[~np.isnan(vr)], vs[~np.isnan(vs)])
            eight[k] = {
                "real_median": float(np.nanmedian(vr)),
                "virtual_median": float(np.nanmedian(vs)),
                "median_ratio": float(np.nanmedian(vs) / (np.nanmedian(vr) + 1e-12)),
                "ks_stat": float(stat), "ks_p": float(p),
            }
        results[f"section_{idx}"] = {
            "n_real": int(real.n_obs), "n_virtual": int(sim.n_obs),
            "morans_i": {"spearman_r": r_mi, "p_value": p_mi},
            "gearys_c": {"spearman_r": r_gc, "p_value": p_gc},
            "eight_metrics": eight,
            "_mi_real": mi_r, "_mi_syn": mi_s,
        }
        print(f"  → Moran's I R={r_mi:.3f} (p={p_mi:.1e}) | "
              f"Geary's C R={r_gc:.3f} (p={p_gc:.1e})", flush=True)
        t.lap(f"第 {idx} 节完成")

    # ---- 3D 体积层面：SSS(1,3,5,7) vs 真实3D(全部7节) vs 稠密体(1,3,5,7+虚拟2,4,6) ----
    print("[3/6] 计算体积层面 Moran's I / Geary's C", flush=True)
    import anndata as ad2
    import pandas as pd

    def stack_3d(adatas):
        xs = []
        for a in adatas:
            if "spatial3d" in a.obsm:
                xs.append(np.asarray(a.obsm["spatial3d"]))
            else:      # 兜底：没有 3D 坐标就用 2D + 该层 z 中位数补齐
                xy2 = np.asarray(a.obsm["spatial"])
                zz2 = np.asarray(a.obs["z"], dtype=float) if "z" in a.obs else \
                    np.full(a.n_obs, float(np.median(a.obsm["spatial"][:, 1])))
                xs.append(np.hstack([xy2, zz2.reshape(-1, 1)]))
        X = np.vstack([dense(a.X) for a in adatas])
        ct = np.concatenate([np.asarray(a.obs[CELL_TYPE_KEY]).astype(str) for a in adatas])
        return ad2.AnnData(X=X.astype(np.float32),
                           obs=pd.DataFrame({CELL_TYPE_KEY: ct})), np.vstack(xs)

    sss_idx = [1, 3, 5, 7]
    a_sss, co_sss = stack_3d([sections[i - 1] for i in sss_idx])
    a_dense, co_dense = stack_3d([sections[i - 1] for i in sss_idx] +
                                 [virtual_sections[i] for i in HOLD_OUT])
    a_real3d, co_real3d = stack_3d(sections)

    mi_real3d = morans_i_gene_wise(dense(a_real3d.X), co_real3d)
    gc_real3d = gearys_c_gene_wise(dense(a_real3d.X), co_real3d)
    mi_sss = morans_i_gene_wise(dense(a_sss.X), co_sss)
    gc_sss = gearys_c_gene_wise(dense(a_sss.X), co_sss)
    mi_dense = morans_i_gene_wise(dense(a_dense.X), co_dense)
    gc_dense = gearys_c_gene_wise(dense(a_dense.X), co_dense)

    r_sss_mi, p_sss_mi = spearman_r(mi_real3d, mi_sss)
    r_sss_gc, p_sss_gc = spearman_r(gc_real3d, gc_sss)
    r_dn_mi, p_dn_mi = spearman_r(mi_real3d, mi_dense)
    r_dn_gc, p_dn_gc = spearman_r(gc_real3d, gc_dense)
    volume = {
        "sparsely_stacked_vs_real3d": {
            "morans_i": {"spearman_r": r_sss_mi, "p_value": p_sss_mi},
            "gearys_c": {"spearman_r": r_sss_gc, "p_value": p_sss_gc},
            "n_cells": int(a_sss.n_obs)},
        "spatialz_dense_vs_real3d": {
            "morans_i": {"spearman_r": r_dn_mi, "p_value": p_dn_mi},
            "gearys_c": {"spearman_r": r_dn_gc, "p_value": p_dn_gc},
            "n_cells": int(a_dense.n_obs)},
        "real3d_n_cells": int(a_real3d.n_obs),
    }
    print(f"  稀疏堆叠 vs 真实3D : Moran's I R={r_sss_mi:.3f}  Geary's C R={r_sss_gc:.3f}",
          flush=True)
    print(f"  SpatialZ 稠密 vs 真实3D: Moran's I R={r_dn_mi:.3f}  Geary's C R={r_dn_gc:.3f}",
          flush=True)
    t.lap("体积层面完成")

    # ---- 出图 ----
    print("[4/6] 画图", flush=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ensure_font_cn()

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))

    ax = axes[0]
    xs = [results[f"section_{i}"]["morans_i"]["spearman_r"] for i in HOLD_OUT]
    ys = [results[f"section_{i}"]["gearys_c"]["spearman_r"] for i in HOLD_OUT]
    x = np.arange(len(HOLD_OUT))
    ax.bar(x - .18, xs, width=.36, label="Moran's I", color="#4C78A8")
    ax.bar(x + .18, ys, width=.36, label="Geary's C", color="#F58518")
    ax.axhline(0.96, ls="--", c="#999", lw=1, label="论文区间下界≈0.95")
    ax.set_xticks(x)
    ax.set_xticklabels([f"第{i}节\n(实{r['n_real']}/虚{r['n_virtual']})"
                        for i, r in zip(HOLD_OUT, results.values())], fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Spearman R（虚拟层 vs 真实层）")
    ax.set_title("复现 Fig.2e：空间自相关一致性")
    ax.legend(fontsize=8); ax.grid(alpha=.25, axis="y")

    ax = axes[1]
    labels = ["稀疏堆叠\n(SSS)", "SpatialZ\n稠密体"]
    mi_vals = [r_sss_mi, r_dn_mi]
    gc_vals = [r_sss_gc, r_dn_gc]
    x = np.arange(2)
    ax.bar(x - .18, mi_vals, width=.36, label="Moran's I", color="#4C78A8")
    ax.bar(x + .18, gc_vals, width=.36, label="Geary's C", color="#F58518")
    for xi, (a_, b_) in enumerate(zip(mi_vals, gc_vals)):
        ax.text(xi - .18, a_ + .01, f"{a_:.3f}", ha="center", fontsize=8)
        ax.text(xi + .18, b_ + .01, f"{b_:.3f}", ha="center", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("与真实 3D 的 Spearman R")
    ax.set_title("复现 Fig.2g/h：插值 vs 稀疏堆叠")
    ax.legend(fontsize=8); ax.grid(alpha=.25, axis="y")

    ax = axes[2]
    keys = list(results[f"section_{HOLD_OUT[0]}"]["eight_metrics"].keys())
    real_m = [results[f"section_{HOLD_OUT[0]}"]["eight_metrics"][k]["real_median"] for k in keys]
    vir_m = [results[f"section_{HOLD_OUT[0]}"]["eight_metrics"][k]["virtual_median"] for k in keys]
    norm_r = [v / (abs(r) + 1e-12) for v, r in zip(real_m, real_m)]
    norm_v = [v / (abs(r) + 1e-12) for v, r in zip(vir_m, real_m)]
    xx = np.arange(len(keys))
    ax.bar(xx - .18, norm_r, width=.36, label="真实层", color="#B0B7C3")
    ax.bar(xx + .18, norm_v, width=.36, label="虚拟层", color="#4C78A8")
    ax.set_xticks(xx)
    ax.set_xticklabels([k.split("_", 1)[0] for k in keys], fontsize=9)
    ax.set_ylabel("相对真实层中位数的比值")
    ax.set_title("复现 Fig.2d：八项统计指标（比值≈1 即一致）")
    ax.legend(fontsize=8); ax.grid(alpha=.25, axis="y")

    fig.suptitle("任务C：SpatialZ 论文 Fig.2 协议复现（STARmap，seed=42，alpha=0.5，fast）",
                 fontsize=12)
    fig.tight_layout()
    save_png("starmap_paper_protocol.png", fig)

    payload = {
        "experiment": "C_STARMAP_PAPER_PROTOCOL",
        "source": "SpatialZ (Nature Methods 2025 / bioRxiv 2024.12.06.627127) Fig.2",
        "seed": 42, "alpha": ALPHA, "nb_iter_max": NB_ITER, "syn_mode": "fast",
        "protocol": {
            "z_keep": list(Z_KEEP),
            "n_sections": N_SECTIONS,
            "held_out_sections": list(HOLD_OUT),
            "note": "论文 Methods：剔除 z6~13 与 z91~94 后切 7 节，移除第 2/4/6 节，"
                    "由 1/3/5/7 推断；SSS=真实 1/3/5/7，稠密体=1/3/5/7+虚拟 2/4/6",
        },
        "paper_values": {
            "fig2e_morans_r": {"slice2": 0.97, "slice4": 0.96, "slice6": 0.98},
            "fig2e_gearys_r": {"slice2": 0.97, "slice4": 0.95, "slice6": 0.97},
            "fig2g_sss_vs_real3d": {"morans_r": 0.98, "gearys_r": 0.98},
            "fig2h_dense_vs_real3d": {"morans_r": 0.99, "gearys_r": 0.99},
        },
        "ours_slice_level": {
            k: {"n_real": v["n_real"], "n_virtual": v["n_virtual"],
                "morans_i": v["morans_i"], "gearys_c": v["gearys_c"],
                "eight_metrics": v["eight_metrics"]}
            for k, v in results.items()},
        "ours_volume_level": volume,
        "runtime_sec": round(t.total(), 1),
    }
    save_json("starmap_paper_protocol.json", payload)

    print("\n==== 任务C 汇总（论文 vs 我们）====")
    for i in HOLD_OUT:
        r = results[f"section_{i}"]
        pm = payload["paper_values"]["fig2e_morans_r"][f"slice{i}"]
        pg = payload["paper_values"]["fig2e_gearys_r"][f"slice{i}"]
        print(f"  第{i}节  Moran's I: 论文 {pm} vs 我们 {r['morans_i']['spearman_r']:.3f}"
              f"   |  Geary's C: 论文 {pg} vs 我们 {r['gearys_c']['spearman_r']:.3f}")
    print(f"  SSS vs 真实3D   : 论文 0.98 vs 我们 {r_sss_mi:.3f}(MI)/{r_sss_gc:.3f}(GC)")
    print(f"  稠密体 vs 真实3D: 论文 0.99 vs 我们 {r_dn_mi:.3f}(MI)/{r_dn_gc:.3f}(GC)")
    print(f"  总耗时 {t.total():.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())