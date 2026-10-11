"""任务 A：STARmap 层级抽稀对标实验（论文第四章 L3 素材）。

设计（确定性，seed=42，不走LLM）：
1. 按 z 坐标把 STARmap 3D 体积分成 7 层（z范围 6~94，每层约 12.6 单位）
2. 对中间层 i=2..6：抽掉该层作ground truth，用相邻两层 (i-1, i+1)
   调 SpatialZ 的 Generate_spatialz(alpha=0.5) 重建该层（虚拟层）
3. 对比：
   ① 逐细胞表达相关（Pearson / Spearman）——按 xy 最近邻把真实细胞与虚拟细胞配对
   ②细胞类型构成差异（leiden）：TV distance + 余弦相似度
   ③ 基因级 Moran's I 的相关（外部文献用它对标，这里一并算）
4. 汇总每层指标 + 中位数/均值，落output/validation/

注意：STARmap 没有 cell_class/Region 列，细胞类型用 obs['leiden']。
      SpatialZ 的 m2_interpolate.gen_one 把 cell_type_key 写死成 'Region'（Visium 用），
      所以这里直接调用 spatialz_src 的 Generate_spatialz，传 cell_type_key='leiden'。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Timer, add_spatialz_to_path, composition_distance,  # noqa: E402
                    ensure_font_cn, morans_i_gene_wise, per_cell_correlation,
                    save_json, save_png, set_seed)

STARMAP = Path(__file__).resolve().parent.parent / "data" / "starmap" / \
    "STARmap_Wang2018three_data_3D_data.h5ad"
N_LAYERS = 7
ALPHA = 0.5
NB_ITER = 1000
CELL_TYPE_KEY = "leiden"


def build_layers(adata, n_layers: int = N_LAYERS):
    """按 z 等宽切成 n_layers 层，返回 [(z_lo, z_hi, adata_layer), ...]。

    每层的 obsm['spatial'] 取 x/y 二维坐标（z 已经用来分层）。
    """
    import anndata as ad

    z = np.asarray(adata.obs["z"], dtype=float)
    edges = np.linspace(z.min(), z.max() + 1e-6, n_layers + 1)
    layers = []
    for i in range(n_layers):
        mask = (z >= edges[i]) & (z < edges[i + 1])
        sub = adata[mask].copy()
        sub.obsm["spatial"] = np.asarray(sub.obs[["x", "y"]].values, dtype=float)
        layers.append((float(edges[i]), float(edges[i + 1]), sub))
        print(f"  层{i + 1}: z[{edges[i]:.1f}, {edges[i + 1]:.1f}) -> {sub.n_obs} 细胞",
              flush=True)
    return layers


def pair_by_nearest(real_xy: np.ndarray, syn_xy: np.ndarray):
    """真实层每个细胞配一个最近的虚拟细胞（空间最近邻配对）。"""
    from sklearn.neighbors import NearestNeighbors
    nn = NearestNeighbors(n_neighbors=1).fit(syn_xy)
    dist, idx = nn.kneighbors(real_xy)
    return idx.ravel(), dist.ravel()


def main() -> int:
    t = Timer("任务A：STARmap 层级抽稀")
    set_seed(42)
    add_spatialz_to_path()
    import anndata as ad
    from SpatialZ import Generate_spatialz
    from scipy import sparse

    print("[1/5] 读取 STARmap 数据", flush=True)
    adata = ad.read_h5ad(STARMAP)
    print(f"  {adata.shape[0]} 细胞 x {adata.shape[1]} 基因", flush=True)
    if CELL_TYPE_KEY not in adata.obs:
        raise SystemExit(f"缺少细胞类型列 {CELL_TYPE_KEY}，实际列：{list(adata.obs.columns)}")

    print("[2/5] 分层", flush=True)
    layers = build_layers(adata)
    t.lap("分层完成")

    per_layer = []
    holdout_idx = range(1, N_LAYERS - 1)          # 第2..6 层（1-based：2..6）
    for i in holdout_idx:
        lo_a, _, a1 = layers[i - 1]
        _, _, a2 = layers[i + 1]
        zlo, zhi, real = layers[i]
        print(f"[3/5] 处理第 {i + 1} 层 (z[{zlo:.1f},{zhi:.1f}) "
              f"上邻 {a1.n_obs} 细胞 / 下邻 {a2.n_obs} 细胞", flush=True)

        sim = Generate_spatialz(
            a1.copy(), a2.copy(),
            adata1_id=f"layer{i}", adata2_id=f"layer{i + 2}",
            alpha=ALPHA, device="cpu", nb_iter_max=NB_ITER, seed=42,
            cell_type_key=CELL_TYPE_KEY, syn_mode="fast", k_sam=50,
            add_obs_list=[CELL_TYPE_KEY], verbose=False)

        def to_dense(x):
            return np.asarray(x.todense()) if sparse.issparse(x) else np.asarray(x)

        real_expr = to_dense(real.X)
        syn_expr = to_dense(sim.X)
        idx, dist = pair_by_nearest(np.asarray(real.obsm["spatial"]),
                                     np.asarray(sim.obsm["spatial"]))
        matched_syn = syn_expr[idx]

        pear = per_cell_correlation(real_expr, matched_syn, "pearson")
        spear = per_cell_correlation(real_expr, matched_syn, "spearman")
        tv, cos, order, p_true, p_syn = composition_distance(
            real.obs[CELL_TYPE_KEY].values, sim.obs[CELL_TYPE_KEY].values, order=order_all())

        mi_real = morans_i_gene_wise(real_expr, np.asarray(real.obsm["spatial"]))
        mi_syn = morans_i_gene_wise(matched_syn, np.asarray(real.obsm["spatial"]))
        ok = ~(np.isnan(mi_real) | np.isnan(mi_syn))
        mi_corr = float(np.corrcoef(mi_real[ok], mi_syn[ok])[0, 1]) if ok.sum() > 2 else float("nan")

        rec = {
            "layer_index": i + 1,
            "z_range": [round(zlo, 2), round(zhi, 2)],
            "n_real_cells": int(real.n_obs),
            "n_syn_cells": int(sim.n_obs),
            "n_cell_types": len(order),
            "pair_dist_median_um": float(np.median(dist)),
            "pearson": {"median": float(np.nanmedian(pear)),
                        "mean": float(np.nanmean(pear)),
                        "q25": float(np.nanpercentile(pear, 25)),
                        "q75": float(np.nanpercentile(pear, 75)),
                        "n": int(np.isfinite(pear).sum())},
            "spearman": {"median": float(np.nanmedian(spear)),
                         "mean": float(np.nanmean(spear)),
                         "q25": float(np.nanpercentile(spear, 25)),
                         "q75": float(np.nanpercentile(spear, 75)),
                         "n": int(np.isfinite(spear).sum())},
            "cell_type_composition": {"tv_distance": tv, "cosine_similarity": cos,
                                     "order": order, "p_true": p_true, "p_syn": p_syn},
            "morans_i_corr_across_genes": mi_corr,
            "_arrays": {"pearson": pear, "spearman": spear, "dist": dist,
                        "labels_real": real.obs[CELL_TYPE_KEY].values},
        }
        per_layer.append(rec)
        print(f"  → Pearson中位 {rec['pearson']['median']:.4f} | "
              f"Spearman中位 {rec['spearman']['median']:.4f} | "
              f"TV {tv:.4f} | 余弦 {cos:.4f} | Moran-I相关 {mi_corr:.4f}", flush=True)
        t.lap(f"第 {i + 1} 层完成")

    # ---------------- 汇总 ----------------
    print("[4/5] 汇总", flush=True)
    def agg(path):
        vals = []
        for r in per_layer:
            cur = r
            for k in path:
                cur = cur[k]
            vals.append(cur)
        vals = np.array(vals, dtype=float)
        return {"per_layer": vals.tolist(), "median": float(np.nanmedian(vals)),
                "mean": float(np.nanmean(vals)), "std": float(np.nanstd(vals))}

    summary = {
        "pearson": agg(["pearson", "median"]),
        "spearman": agg(["spearman", "median"]),
        "tv_distance": agg(["cell_type_composition", "tv_distance"]),
        "cosine_similarity": agg(["cell_type_composition", "cosine_similarity"]),
        "morans_i_corr": agg(["morans_i_corr_across_genes"]),
        "pair_dist_median": agg(["pair_dist_median_um"]),
    }
    payload = {
        "experiment": "A_STARMAP_LAYER_HOLDOUT",
        "seed": 42,
        "data": str(STARMAP),
        "n_layers": N_LAYERS,
        "alpha": ALPHA,
        "nb_iter_max": NB_ITER,
        "syn_mode": "fast",
        "cell_type_key": CELL_TYPE_KEY,
        "pairing": "真实层细胞按 xy 最近邻配对到虚拟层细胞",
        "holdout_layers": [r["layer_index"] for r in per_layer],
        "summary": summary,
        "per_layer": [{k: v for k, v in r.items() if k != "_arrays"} for r in per_layer],
        "runtime_sec": round(t.total(), 1),
    }
    save_json("starmap_layer_holdout.json", payload)

    # ---------------- 出图 ----------------
    print("[5/5] 画图", flush=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ensure_font_cn()

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    colors = ["#4C78A8", "#F58518", "#54A24B"]

    ax = axes[0]
    data = [r["_arrays"]["pearson"] for r in per_layer]
    parts = ax.violinplot(data, showmeans=True, showextrema=False)
    for pc, c in zip(parts["bodies"], colors):
        pc.set_facecolor(c); pc.set_alpha(.75); pc.set_edgecolor("none")
    ax.set_xticks(range(1, len(data) + 1))
    ax.set_xticklabels([f"第{r['layer_index']}层\nz{r['z_range'][0]:.0f}-{r['z_range'][1]:.0f}"
                        for r in per_layer], fontsize=8)
    ax.axhline(summary["pearson"]["median"], ls="--", c="#888",
               label=f"总中位 {summary['pearson']['median']:.3f}")
    ax.set_ylabel("逐细胞 Pearson 相关"); ax.set_title("虚拟层 vs 真实层：表达相关")
    ax.legend(fontsize=8, loc="lower right"); ax.grid(alpha=.25, axis="y")

    ax = axes[1]
    data = [r["_arrays"]["spearman"] for r in per_layer]
    parts = ax.violinplot(data, showmeans=True, showextrema=False)
    for pc, c in zip(parts["bodies"], colors):
        pc.set_facecolor(c); pc.set_alpha(.75); pc.set_edgecolor("none")
    ax.set_xticks(range(1, len(data) + 1))
    ax.set_xticklabels([f"第{r['layer_index']}层" for r in per_layer], fontsize=8)
    ax.axhline(summary["spearman"]["median"], ls="--", c="#888",
               label=f"总中位 {summary['spearman']['median']:.3f}")
    ax.set_ylabel("逐细胞 Spearman 相关"); ax.set_title("秩相关（对表达幅值更鲁棒）")
    ax.legend(fontsize=8, loc="lower right"); ax.grid(alpha=.25, axis="y")

    ax = axes[2]
    x = np.arange(1, len(per_layer) + 1)
    ax.bar(x - .18, [r["cell_type_composition"]["tv_distance"] for r in per_layer],
           width=.36, color="#E45756", label="TV distance（越小越像）")
    ax.bar(x + .18, [r["cell_type_composition"]["cosine_similarity"] for r in per_layer],
           width=.36, color="#4C78A8", label="余弦相似度（越大越像）")
    ax.set_xticks(x); ax.set_xticklabels([f"第{r['layer_index']}层" for r in per_layer],
                                         fontsize=8)
    ax.set_title("细胞类型（leiden）构成差异")
    ax.legend(fontsize=8); ax.grid(alpha=.25, axis="y")
    ax.set_ylim(0, 1)

    fig.suptitle(f"任务A：STARmap 层级抽稀对标（seed=42, alpha={ALPHA}, "
                 f"合成模式=fast）", fontsize=12)
    fig.tight_layout()
    save_png("starmap_layer_holdout.png", fig)

    print("\n==== 任务A 汇总 ====")
    print(f"  逐细胞Pearson  中位 {summary['pearson']['median']:.4f}"
          f"（各层 {np.round(summary['pearson']['per_layer'], 3)}）")
    print(f"  逐细胞 Spearman 中位 {summary['spearman']['median']:.4f}"
          f"（各层 {np.round(summary['spearman']['per_layer'], 3)}）")
    print(f"  细胞类型 TV    中位 {summary['tv_distance']['median']:.4f}")
    print(f"  构成余弦相似度 中位 {summary['cosine_similarity']['median']:.4f}")
    print(f"  基因级 Moran-I 相关 中位 {summary['morans_i_corr']['median']:.4f}")
    print(f"  总耗时 {t.total():.1f}s")
    return 0


_ORDER_CACHE = None


def order_all() -> list:
    """所有层共有的 leiden 类别顺序（保证各层构成可比）。"""
    global _ORDER_CACHE
    if _ORDER_CACHE is None:
        import anndata as ad
        a = ad.read_h5ad(STARMAP, backed="r")
        _ORDER_CACHE = sorted(a.obs[CELL_TYPE_KEY].astype(str).unique().tolist(),
                              key=lambda s: int(s) if s.isdigit() else 999)
    return _ORDER_CACHE


if __name__ == "__main__":
    sys.exit(main())