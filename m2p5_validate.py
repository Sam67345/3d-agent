"""M2.5 抽稀重建验证指标：虚拟中间层 vs held-out 真实切片 151674。

三项指标（结果存 output/holdout_metrics.json）：
  1. 分层构成：Region 比例表 + 每层绝对差（百分点）
  2. 分层平均表达：每个 Region 平均表达向量（虚拟 vs 真实）的 Spearman 相关
  3. 逐点对比：每个真实 spot 用虚拟层中空间最近且同 Region 的 10 个 spot 均值
     作为预测，全基因 Spearman；报告中位数与分位数分布

可视化：151673 | 虚拟中间层 | 151674 三张 Region 空间散点图并排
（output/holdout_comparison.png）

复用：m2p5_holdout.build_inputs（同一预处理面板）、m2_validate.layer_fractions。
"""

import json
import sys
from pathlib import Path

import anndata as ad
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))

from m2_validate import layer_fractions  # noqa: E402
from m2p5_holdout import HOLD_OUT, SID_A, SID_B, SID_HELD, build_inputs  # noqa: E402

OUT_DIR = Path(__file__).parent / "output"
K_NEIGH = 10
LAYERS = ["Layer1", "Layer2", "Layer3", "Layer4", "Layer5", "Layer6", "WM"]


def metric_composition(sim, held):
    """指标1：分层构成比例与绝对差（百分点）。"""
    fs = layer_fractions(sim.obs).reindex(LAYERS)
    fh = layer_fractions(held.obs).reindex(LAYERS)
    diff_pp = ((fs - fh).abs() * 100).round(2)
    return {
        "virtual_frac": fs.round(4).to_dict(),
        "heldout_frac": fh.round(4).to_dict(),
        "abs_diff_percentage_points": diff_pp.to_dict(),
        "mean_abs_diff_pp": float(diff_pp.mean()),
    }


def metric_region_expression(sim, held):
    """指标2：逐 Region 平均表达向量 Spearman。"""
    out = {}
    sim_mean = pd.DataFrame(sim.X, columns=sim.var_names).groupby(
        sim.obs["Region"].values).mean()
    held_mean = pd.DataFrame(held.X, columns=held.var_names).groupby(
        held.obs["Region"].values).mean()
    for layer in LAYERS:
        if layer in sim_mean.index and layer in held_mean.index:
            rho, p = spearmanr(sim_mean.loc[layer], held_mean.loc[layer])
            out[layer] = {"rho": round(float(rho), 4), "pvalue": float(p)}
    return out


def metric_per_spot(sim, held):
    """指标3：逐点 10-NN 同 Region 均值预测的 Spearman 分布。"""
    # 按 Region 分组建 KDTree（虚拟层）
    trees, expr = {}, {}
    sim_X = np.asarray(sim.X)
    for layer, idx in sim.obs.groupby("Region").indices.items():
        expr[layer] = sim_X[idx]
        trees[layer] = cKDTree(sim.obsm["spatial"][idx])

    held_X = np.asarray(held.X)
    rhos = np.full(held.n_obs, np.nan)
    for i, layer in enumerate(held.obs["Region"].values):
        if layer not in trees:
            continue
        _, nn = trees[layer].query(held.obsm["spatial"][i],
                                   k=min(K_NEIGH, expr[layer].shape[0]))
        pred = expr[layer][nn].mean(axis=0)
        rho, _ = spearmanr(held_X[i], pred)
        rhos[i] = rho

    valid = rhos[~np.isnan(rhos)]
    qs = np.percentile(valid, [5, 25, 50, 75, 95])
    return {
        "k_neighbors": K_NEIGH,
        "n_spots": int(len(valid)),
        "rho_median": round(float(np.median(valid)), 4),
        "rho_mean": round(float(valid.mean()), 4),
        "rho_std": round(float(valid.std()), 4),
        "rho_p5": round(float(qs[0]), 4),
        "rho_p25": round(float(qs[1]), 4),
        "rho_p75": round(float(qs[3]), 4),
        "rho_p95": round(float(qs[4]), 4),
        "frac_rho_gt_0.5": round(float((valid > 0.5).mean()), 4),
    }


def plot_comparison(a, sim, held):
    """151673 | 虚拟中间层 | 151674 并排 Region 空间散点图。"""
    cmap = {l: c for l, c in zip(LAYERS, plt.cm.tab10.colors[:len(LAYERS)])}
    fig, axes = plt.subplots(1, 3, figsize=(21, 7))
    panels = [(a, f"{SID_A} (input)"),
              (sim, "Virtual mid (SpatialZ)"),
              (held, f"{SID_HELD} (held-out truth)")]
    for ax, (adata, title) in zip(axes, panels):
        xy = adata.obsm["spatial"]
        regions = adata.obs["Region"].astype(str).values
        for layer in LAYERS:
            m = regions == layer
            if m.any():
                ax.scatter(xy[m, 0], -xy[m, 1], s=3, c=[cmap[layer]],
                           label=layer, alpha=0.7)
        ax.set_title(title, fontsize=14)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="right", fontsize=11, markerscale=4,
               title="Region", bbox_to_anchor=(1.0, 0.5))
    fig.suptitle("Held-out reconstruction: DLPFC 151673 | virtual | 151674",
                 fontsize=16)
    fig.tight_layout(rect=[0, 0, 0.93, 0.95])
    out = OUT_DIR / "holdout_comparison.png"
    fig.savefig(out, dpi=150)
    return out


def main():
    sim = ad.read_h5ad(HOLD_OUT / "virtual_mid_151674.h5ad")
    held = ad.read_h5ad(HOLD_OUT / "held_151674_panel.h5ad")
    a, _, _ = build_inputs()  # 151673（同一面板），仅用于对比图

    metrics = {
        "experiment": "held-out reconstruction (SpatialZ)",
        "inputs": {"above": SID_A, "below": SID_B, "held_out": SID_HELD},
        "n_genes_panel": int(sim.n_vars),
        "composition": metric_composition(sim, held),
        "region_expression_spearman": metric_region_expression(sim, held),
        "per_spot_prediction": metric_per_spot(sim, held),
    }

    out_json = OUT_DIR / "holdout_metrics.json"
    out_json.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    out_png = plot_comparison(a, sim, held)

    # 控制台摘要
    print("=== 指标1: 分层构成（虚拟 vs 151674 真值）===")
    comp = metrics["composition"]
    for layer in LAYERS:
        print(f"  {layer:8s} 虚拟 {comp['virtual_frac'][layer]:.3f} | "
              f"真值 {comp['heldout_frac'][layer]:.3f} | "
              f"差 {comp['abs_diff_percentage_points'][layer]:.2f} pp")
    print(f"  平均绝对差: {comp['mean_abs_diff_pp']:.2f} 百分点")

    print("\n=== 指标2: 逐 Region 平均表达 Spearman ===")
    for layer, r in metrics["region_expression_spearman"].items():
        print(f"  {layer:8s} rho={r['rho']:.4f}")

    print("\n=== 指标3: 逐点 10-NN 预测 Spearman ===")
    ps = metrics["per_spot_prediction"]
    print(f"  n={ps['n_spots']} 中位数={ps['rho_median']:.4f} "
          f"均值={ps['rho_mean']:.4f} 标准差={ps['rho_std']:.4f}")
    print(f"  P5={ps['rho_p5']:.4f} P25={ps['rho_p25']:.4f} "
          f"P75={ps['rho_p75']:.4f} P95={ps['rho_p95']:.4f}")
    print(f"  rho>0.5 占比: {ps['frac_rho_gt_0.5']:.2%}")

    print(f"\n已保存: {out_json}\n        {out_png}")


if __name__ == "__main__":
    main()
