"""任务 B：DLPFC 切片内 spot 抽稀重建（论文第四章 L3 素材·方案 B）。

背景：之前用"跨切片 holdout"（151673+151675 插值出151674）做验证有缺陷——
被重建的那层本身参与了重建，等于拿答案推答案。本实验改成**切片内抽稀**，
是空间转录组里更标准的 imputation 评测范式。

方案说明（任务书要求"以教程实现为准，汇报里写明"）：
  仓库内没有 SpatialZ 官方教程 notebook（已检索确认），SpatialZ 只提供"两层之间插值"
  的机制，没有"单层内缺失表达推断"的独立接口。因此这里采用**切片内抽稀**：
    · seed=42 随机抽掉 70% spot 的表达值 → 待重建集
    · 剩余 30% → 已知参考集
    · 用 SpatialZ 的快速表达合成机制 synthesize_gene_expression_fast()
      （Generate_spatialz 内部用的同一个函数：10-NN 内优先取同 Region 的邻居求均值）
      以已知 30% 为参考，重建被抽掉的 70%
    · 基线：普通 kNN 空间平滑（k=6，不看细胞类型），在同样数据上跑
  这样 SpatialZ 的"细胞类型感知"这一项优势能被单独度量出来。

指标：
    ① 逐 spot 表达相关（Pearson + Spearman）：总体中位数/均值
    ② 分脑区（Region 人工分层注释）重建精度
    ③ 与 kNN 基线对比 + 提升幅度

基因面板：原始 33,538 个基因过重（稠密化 470MB 且合成慢），
按 m2_interpolate 的既有做法取 top 2000 HVG（种子固定，可复现）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Timer, add_spatialz_to_path, ensure_font_cn,  # noqa: E402
                    per_cell_correlation, save_json, save_png, set_seed)

DATA = Path(__file__).resolve().parent.parent / "data" / \
    "151674_filtered_feature_bc_matrix.h5ad"
HOLDOUT_FRAC = 0.70          # 抽掉 70%
N_HVG = 2000                  # 公共基因面板大小（与 m2_interpolate 的 N_HVG 一致）
K_SAM = 50                    # SpatialZ fast 模式同类型邻居上限
K_BASELINE = 6                # 基线 kNN 的 k
CELL_TYPE_KEY = "Region"


def load_and_prepare():
    """读151674，做归一化 + top2000 HVG 子集，返回稠密 float32 AnnData。"""
    import anndata as ad
    import scanpy as sc
    import scipy.sparse as sp

    a = ad.read_h5ad(DATA)
    if sp.issparse(a.X):
        a.X = a.X.toarray()
    a.X = np.asarray(a.X, dtype=np.float32)
    sc.pp.filter_genes(a, min_cells=3)
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    sc.pp.highly_variable_genes(a, n_top_genes=N_HVG, flavor="seurat")
    a = a[:, a.var["highly_variable"]].copy()
    a.X = np.asarray(a.X, dtype=np.float32)
    # 注意：必须转成 numpy object 数组。若保留 pandas Arrow 扩展类型，
    # 后面 (ct == r) 得到的是 ArrowBooleanArray，没有 .sum() 方法会报错。
    a.obs[CELL_TYPE_KEY] = (a.obs[CELL_TYPE_KEY].astype("string")
                            .fillna("Unknown").astype(str).values)
    print(f"  预处理后：{a.shape[0]} spot x {a.shape[1]} HVG；"
          f"脑区 {a.obs[CELL_TYPE_KEY].nunique()} 类", flush=True)
    return a


def spatialz_impute(known_expr, known_xy, known_ct,
                    query_xy, query_ct, k_sam=K_SAM):
    """SpatialZ 快速表达合成：10-NN 内优先取同细胞类型邻居求均值。

    这里直接调 SpatialZ 源码里的 synthesize_gene_expression_fast（不修改其文件），
    它是 Generate_spatialz 在 syn_mode='fast' 下走的同一条代码路径。
    参考集只传一份时，源码里 concatenate([]) 在新版 anndata 会报错，
    因此传两份相同的参考集再取其一—结果等价，且与官方多切片用法兼容。
    """
    import anndata as ad
    import pandas as pd
    from SpatialZ import synthesize_gene_expression_fast

    ref = ad.AnnData(X=known_expr.copy(),
                     obs=pd.DataFrame({CELL_TYPE_KEY: pd.Categorical(known_ct)}))
    ref.obsm["spatial"] = known_xy
    query = ad.AnnData(
        X=np.zeros((query_xy.shape[0], known_expr.shape[1]), dtype=np.float32),
        obs=pd.DataFrame({CELL_TYPE_KEY: pd.Categorical(query_ct)}))
    query.obsm["spatial"] = query_xy
    out = synthesize_gene_expression_fast([ref, ref], query, CELL_TYPE_KEY, k_sam)
    return np.asarray(out.X, dtype=np.float64)


def knn_baseline(known_expr, known_xy, query_xy, k=K_BASELINE):
    """基线：普通 kNN 空间平滑（不看细胞类型），k=6。"""
    from sklearn.neighbors import NearestNeighbors
    nn = NearestNeighbors(n_neighbors=k).fit(known_xy)
    _, idx = nn.kneighbors(query_xy)
    return known_expr[idx].mean(axis=1)


def main() -> int:
    t = Timer("任务B：DLPFC 切片内 spot 抽稀重建")
    rng = set_seed(42)
    add_spatialz_to_path()

    print("[1/5] 读取并预处理 151674", flush=True)
    a = load_and_prepare()
    n = a.n_obs
    expr = np.asarray(a.X, dtype=np.float64)
    xy = np.asarray(a.obsm["spatial"], dtype=np.float64)
    ct = a.obs[CELL_TYPE_KEY].values
    t.lap("预处理完成")

    print("[2/5] 按 seed=42 划分 70% 待重建 / 30% 已知", flush=True)
    perm = rng.permutation(n)
    n_hold = int(round(n * HOLDOUT_FRAC))
    hold_idx, known_idx = perm[:n_hold], perm[n_hold:]
    print(f"  待重建 {len(hold_idx)} spot / 已知 {len(known_idx)} spot", flush=True)

    print("[3/5] SpatialZ fast 合成重建", flush=True)
    rec_syn = spatialz_impute(expr[known_idx], xy[known_idx], ct[known_idx],
                              xy[hold_idx], ct[hold_idx])
    t.lap("SpatialZ 重建完成")

    truth = expr[hold_idx]

    print("[4/5] kNN 基线（k=6）+ k 敏感性诊断", flush=True)
    rec_knn = knn_baseline(expr[known_idx], xy[known_idx], xy[hold_idx], K_BASELINE)
    t.lap("基线完成")

    # 诊断：SpatialZ fast 在 10-NN 内"只用同类型邻居"，实际平均的邻居数<=10；
    # 基线平均 k 个最近邻（不看类型）。平均更多邻居可降噪，会抬高 Pearson。
    # 这里扫一遍 k，看基线随k 的走势，用于解释上面的反直觉结果（不做参数挑选）。
    ks = [3, 6, 10, 20, 50]
    baseline_sensitivity = {}
    for k in ks:
        rec_k = knn_baseline(expr[known_idx], xy[known_idx], xy[hold_idx], k)
        baseline_sensitivity[str(k)] = {
            "pearson_median": float(np.nanmedian(
                per_cell_correlation(truth, rec_k, "pearson"))),
            "note": f"k={k} 时每个待重建 spot 由 {k} 个最近邻均值预测",
        }
    t.lap("k 敏感性完成")

    res = {}
    for name, rec in (("spatialz_fast", rec_syn), ("knn_baseline", rec_knn)):
        pear = per_cell_correlation(truth, rec, "pearson")
        spear = per_cell_correlation(truth, rec, "spearman")
        res[name] = {"pearson": pear, "spearman": spear}

    #分脑区统计
    regions = sorted(set(ct[hold_idx].tolist()))
    by_region = {}
    for r in regions:
        m = ct[hold_idx] == r
        if m.sum() < 5:
            continue
        by_region[str(r)] = {
            "n_spots": int(m.sum()),
            "spatialz_pearson_median": float(np.nanmedian(res["spatialz_fast"]["pearson"][m])),
            "knn_pearson_median": float(np.nanmedian(res["knn_baseline"]["pearson"][m])),
            "spatialz_spearman_median": float(np.nanmedian(res["spatialz_fast"]["spearman"][m])),
            "knn_spearman_median": float(np.nanmedian(res["knn_baseline"]["spearman"][m])),
        }

    def summ(key, method):
        v = res[key][method]
        return {"median": float(np.nanmedian(v)), "mean": float(np.nanmean(v)),
                "q25": float(np.nanpercentile(v, 25)), "q75": float(np.nanpercentile(v, 75)),
                "n": int(np.isfinite(v).sum())}

    summary = {
        "spatialz_fast": {"pearson": summ("spatialz_fast", "pearson"),
                          "spearman": summ("spatialz_fast", "spearman")},
        "knn_baseline": {"pearson": summ("knn_baseline", "pearson"),
                         "spearman": summ("knn_baseline", "spearman")},
    }
    gain = {}
    for m in ("pearson", "spearman"):
        a_med = summary["spatialz_fast"][m]["median"]
        b_med = summary["knn_baseline"][m]["median"]
        gain[m] = {"absolute": a_med - b_med,
                   "relative_pct": (a_med - b_med) / b_med * 100 if b_med else float("nan")}

    payload = {
        "experiment": "B_DLPFC_SPOT_HOLDOUT",
        "seed": 42,
        "data": str(DATA),
        "sample": "151674",
        "scheme": "within-slice 70% spot holdout; SpatialZ synthesize_gene_expression_fast"
                  "（与 Generate_spatialz fast 模式同一代码路径）重建 30% 已知spot",
        "n_spots_total": int(n),
        "n_holdout": int(len(hold_idx)),
        "n_known": int(len(known_idx)),
        "n_genes_hvg": int(a.n_vars),
        "k_sam": K_SAM,
        "k_baseline": K_BASELINE,
        "cell_type_key": CELL_TYPE_KEY,
        "summary": summary,
        "spatialz_gain_over_knn": gain,
        "by_region": by_region,
        "baseline_k_sensitivity": baseline_sensitivity,
        "note_spatialz_vs_knn": (
            "SpatialZ fast 在 10-NN 内只取同 Region 邻居求均值，实际平均邻居数 <=10；"
            "kNN 基线平均 k 个最近邻（不区分类型）。切片内随机抽稀时空间近邻本身信息量高，"
            "平均更多邻居降噪反而更稳，因此基线略占优。此处如实记录，未做任何参数挑选。"),
        "runtime_sec": round(t.total(), 1),
    }
    save_json("spot_holdout_metrics.json", payload)

    print("[5/5] 画图", flush=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ensure_font_cn()

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))

    ax = axes[0]
    data = [res["knn_baseline"]["pearson"], res["spatialz_fast"]["pearson"]]
    parts = ax.violinplot(data, showmeans=True)
    for pc, c in zip(parts["bodies"], ["#B0B7C3", "#4C78A8"]):
        pc.set_facecolor(c); pc.set_alpha(.8); pc.set_edgecolor("none")
    ax.set_xticks([1, 2])
    ax.set_xticklabels([f"kNN 基线\n中位 {summary['knn_baseline']['pearson']['median']:.3f}",
                        f"SpatialZ fast\n中位 {summary['spatialz_fast']['pearson']['median']:.3f}"],
                       fontsize=9)
    ax.set_ylabel("逐 spot Pearson 相关"); ax.set_title("整体重建精度分布")
    ax.grid(alpha=.25, axis="y")

    ax = axes[1]
    data = [res["knn_baseline"]["spearman"], res["spatialz_fast"]["spearman"]]
    parts = ax.violinplot(data, showmeans=True)
    for pc, c in zip(parts["bodies"], ["#B0B7C3", "#54A24B"]):
        pc.set_facecolor(c); pc.set_alpha(.8); pc.set_edgecolor("none")
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["kNN 基线", "SpatialZ fast"], fontsize=9)
    ax.set_ylabel("逐 spot Spearman 相关"); ax.set_title("秩相关")
    ax.grid(alpha=.25, axis="y")

    ax = axes[2]
    regs = list(by_region.keys())
    x = np.arange(len(regs))
    ax.bar(x - .18, [by_region[r]["knn_pearson_median"] for r in regs], width=.36,
           color="#B0B7C3", label="kNN 基线")
    ax.bar(x + .18, [by_region[r]["spatialz_pearson_median"] for r in regs], width=.36,
           color="#4C78A8", label="SpatialZ fast")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r}\n(n={by_region[r]['n_spots']})" for r in regs],
                       fontsize=8, rotation=20)
    ax.set_ylabel("Pearson 中位数"); ax.set_title("分脑区重建精度")
    ax.legend(fontsize=8); ax.grid(alpha=.25, axis="y")

    fig.suptitle("任务B：DLPFC 151674 切片内 70% spot 抽稀重建（seed=42, 2000 HVG）",
                 fontsize=12)
    fig.tight_layout()
    save_png("spot_holdout.png", fig)

    print("\n==== 任务B 汇总 ====")
    print(f"  SpatialZ fast  Pearson中位 {summary['spatialz_fast']['pearson']['median']:.4f}"
          f" / Spearman {summary['spatialz_fast']['spearman']['median']:.4f}")
    print(f"  kNN 基线       Pearson中位 {summary['knn_baseline']['pearson']['median']:.4f}"
          f" / Spearman {summary['knn_baseline']['spearman']['median']:.4f}")
    print(f"  绝对提升 Pearson {gain['pearson']['absolute']:+.4f}"
          f"（相对 {gain['pearson']['relative_pct']:+.1f}%）")
    print("  基线 k 敏感性（Pearson中位）：" +
          "  ".join(f"k={k}={v['pearson_median']:.3f}" for k, v in baseline_sensitivity.items()))
    print(f"  总耗时 {t.total():.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())