"""任务 D：MERFISH 论文协议复现（Fig. 3）。

论文口径（Methods 原文）：
  · 数据：小鼠下丘脑视前区 MERFISH，5 张相邻切片（Data 2），z 间距比 STARmap 大
  · 做法：在每两张相邻真实切片之间生成 **3 个虚拟切片**
  · 验证：(a) 虚拟+真实切片的细胞类型分布 / 空间域
          (b) 下游空间域聚类（STAGATE / BINARY）的 ARI、NMI：
              **加入虚拟切片后的稠密数据优于原始稀疏数据**

口径偏差（必须声明）：
  论文用 STAGATE 与 BINARY 两种空间聚类方法。本项目 venv 内未安装这两个包，
  且本任务不允许安装新包，故改用固定随机种子的 KMeans（50 维 PCA 上，k=8 对齐
  真实 domain 类别数）。两臂（稀疏 / 稠密）用**同一聚类器**，因此
  "稠密是否优于稀疏"这一相对比较仍然成立，但不能与论文的 ARI/NMI 绝对值直接对比。

ARI/NMI 的评估口径：分别对两臂聚类后，只在**有真值标注的真实细胞**上评估
（虚拟细胞本身没有人工标注），与论文"评估识别出的空间域"意图一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Timer, add_spatialz_to_path, composition_distance,  # noqa: E402
                    ensure_font_cn, save_json, save_png, set_seed,
                    shim_anndata_concatenate)

MERFISH_DIR = Path(__file__).resolve().parent.parent / "data" / "merfish_five"
SECTIONS = ["merfish_4_paste", "merfish_9_paste", "merfish_14_paste",
            "merfish_19_paste", "merfish_24_paste"]
N_VIRT_PER_GAP = 3           # 论文：每对相邻切片之间 3 个虚拟切片
NB_ITER = 1000
CELL_TYPE_KEY = "cell_class"
DOMAIN_KEY = "domain"
N_CLUSTERS = 8               # 与 domain 类别数一致
PCA_DIM = 50


def load_sections():
    import anndata as ad
    out = []
    for name in SECTIONS:
        p = MERFISH_DIR / f"{name}.h5ad"
        a = ad.read_h5ad(p)
        # 统一列类型（pandas Arrow 扩展类型后面比较会出问题的）
        a.obs[CELL_TYPE_KEY] = a.obs[CELL_TYPE_KEY].astype(str).values
        a.obs[DOMAIN_KEY] = a.obs[DOMAIN_KEY].astype(str).values
        a.X = np.asarray(a.X.todense() if hasattr(a.X, "todense") else a.X,
                         dtype=np.float32)
        out.append(a)
        print(f"  {name}: {a.n_obs} 细胞 x {a.n_vars} 基因", flush=True)
    return out


def cluster_eval(expr, truth_mask, truth, k=N_CLUSTERS, seed=42):
    """在 PCA 空间做 KMeans，返回 ARI / NMI（只在 truth_mask 为真的细胞上评估）。"""
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    logX = np.log1p(expr)
    n_comp = min(PCA_DIM, min(logX.shape) - 1)
    pcs = PCA(n_components=n_comp, random_state=seed).fit_transform(logX)
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(pcs)
    lab = km.labels_
    return (float(adjusted_rand_score(truth[truth_mask], lab[truth_mask])),
            float(normalized_mutual_info_score(truth[truth_mask], lab[truth_mask])),
            lab)


def main() -> int:
    t = Timer("任务D：MERFISH 论文协议复现")
    set_seed(42)
    add_spatialz_to_path()
    import anndata as ad
    from SpatialZ import Generate_multiple_spatialz, Generate_spatialz
    from scipy import sparse as sp

    print(f"[0/5] {shim_anndata_concatenate()}", flush=True)
    print("[1/5] 读取 5 张 MERFISH 切片", flush=True)
    sections = load_sections()
    t.lap("读取完成")

    print("[2/5] 每对相邻切片之间生成 3 个虚拟切片", flush=True)
    cache = Path(__file__).resolve().parent.parent / "output" / "validation" / \
        "_cache_exp_d"
    cache.mkdir(parents=True, exist_ok=True)
    virtuals = []
    for i in range(len(sections) - 1):
        up, dn = sections[i], sections[i + 1]
        cpath = cache / f"pair_{i}_{SECTIONS[i]}_{SECTIONS[i + 1]}.h5ad"
        if cpath.exists():
            gen = ad.read_h5ad(cpath)
            sid = gen.obs["slice_id"].astype(str).values
            for sname in sorted(set(sid)):
                virtuals.append(gen[sid == sname].copy())
            print(f"  {SECTIONS[i]} ↔ {SECTIONS[i + 1]}：命中缓存 "
                  f"({len(set(sid))} 层)", flush=True)
            t.lap(f"  第 {i + 1} 对（缓存）")
            continue
        print(f"  {SECTIONS[i]} ↔ {SECTIONS[i + 1]}：生成 {N_VIRT_PER_GAP} 层", flush=True)
        # 不用 Generate_multiple_spatialz：其内部 `s.obs['slice_id'][0]` 在 pandas 2.x
        # 会 KeyError（标签索引），且 AnnData.concatenate 已被 anndata 0.13 移除。
        # 改为按论文描述逐层调用 Generate_spatialz（alpha=1/4, 2/4, 3/4），
        # 与本项目 interpolate_multiple 技能的做法一致。
        # 注意：obs 列取值必须用 .iloc[0]（pandas 2.x）或直接用已知层名，不能用 [0]。
        layers, names = [], []
        for k in range(1, N_VIRT_PER_GAP + 1):
            name = f"{SECTIONS[i]}_{SECTIONS[i + 1]}_v{k}"
            lpath = cache / f"layer_{i}_{k}.h5ad"
            if lpath.exists():
                sim_k = ad.read_h5ad(lpath)
                print(f"    {name}: 命中缓存 ({sim_k.n_obs} 细胞)", flush=True)
            else:
                alpha_k = k / (N_VIRT_PER_GAP + 1)
                sim_k = Generate_spatialz(
                    up.copy(), dn.copy(),
                    adata1_id=SECTIONS[i], adata2_id=SECTIONS[i + 1],
                    alpha=alpha_k, device="cpu", nb_iter_max=NB_ITER, seed=42,
                    cell_type_key=CELL_TYPE_KEY, syn_mode="fast", k_sam=50,
                    add_obs_list=[CELL_TYPE_KEY, DOMAIN_KEY], verbose=False)
                sim_k.obs["slice_id"] = name
                sim_k.write_h5ad(lpath)          # 先落盘，后续出错也不丢已算好的层
                print(f"    alpha={alpha_k:.2f} -> {sim_k.n_obs} 细胞 [已缓存]", flush=True)
            layers.append(sim_k)
            names.append(name)
        gen = ad.concat(layers, label="slice_id", keys=names)
        virtuals.extend(layers)
        print(f"    -> 本对共 {len(layers)} 层虚拟切片", flush=True)
        t.lap(f"  第 {i + 1} 对完成")

    print(f"[3/5] 稀疏臂 {sum(a.n_obs for a in sections)} 细胞 / "
          f"稠密臂 {sum(a.n_obs for a in sections) + sum(a.n_obs for a in virtuals)} 细胞",
          flush=True)

    def stack(adatas):
        X = np.vstack([np.asarray(a.X) for a in adatas])
        ct = np.concatenate([np.asarray(a.obs[CELL_TYPE_KEY]).astype(str) for a in adatas])
        dm = np.concatenate([np.asarray(a.obs[DOMAIN_KEY]).astype(str) for a in adatas])
        is_real = np.concatenate([np.ones(a.n_obs, bool) for a in adatas])
        return X, ct, dm, is_real

    X_s, ct_s, dm_s, real_s = stack(sections)
    X_d, ct_d, dm_d, real_d = stack(sections + virtuals)

    ct_tv, ct_cos, ct_order, _, _ = composition_distance(ct_s, ct_d)
    dm_tv, dm_cos, dm_order, _, _ = composition_distance(dm_s, dm_d)
    print(f"  细胞类型构成 TV={ct_tv:.4f} 余弦={ct_cos:.4f}", flush=True)
    print(f"  空间域构成   TV={dm_tv:.4f} 余弦={dm_cos:.4f}", flush=True)

    print("[4/5] 下游空间域聚类 ARI / NMI", flush=True)
    ari_s, nmi_s, _ = cluster_eval(X_s, real_s, dm_s)
    ari_d, nmi_d, _ = cluster_eval(X_d, real_d, dm_d)
    print(f"  稀疏(5 真实切片)     ARI={ari_s:.4f}  NMI={nmi_s:.4f}", flush=True)
    print(f"  稠密(5真实+{len(virtuals)}虚拟) ARI={ari_d:.4f}  NMI={nmi_d:.4f}", flush=True)
    t.lap("聚类评估完成")

    print("[5/5] 画图", flush=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ensure_font_cn()

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    ax = axes[0]
    metrics = ["ARI", "NMI"]
    sparse_v = [ari_s, nmi_s]
    dense_v = [ari_d, nmi_d]
    x = np.arange(2)
    ax.bar(x - .18, sparse_v, width=.36, color="#B0B7C3", label="稀疏（5 真实切片）")
    ax.bar(x + .18, dense_v, width=.36, color="#4C78A8",
           label=f"稠密（+{len(virtuals)} 虚拟切片）")
    for xi, (a_, b_) in enumerate(zip(sparse_v, dense_v)):
        ax.text(xi - .18, a_ + .008, f"{a_:.3f}", ha="center", fontsize=9)
        ax.text(xi + .18, b_ + .008, f"{b_:.3f}", ha="center", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(metrics, fontsize=10)
    ax.set_ylim(0, 1.0); ax.set_ylabel("与真实空间域标注的一致性")
    ax.set_title("复现 Fig.3j/k：加入虚拟切片后下游聚类是否提升\n"
                 "（代理聚类器 KMeans，非论文的 STAGATE/BINARY）", fontsize=10)
    ax.legend(fontsize=8, loc="lower right"); ax.grid(alpha=.25, axis="y")

    ax = axes[1]
    cats = ct_order
    p_real = np.array([(ct_s == c).mean() for c in cats])
    p_vir = np.array([(ct_d == c).mean() for c in cats])
    xx = np.arange(len(cats))
    ax.bar(xx - .2, p_real, width=.4, color="#B0B7C3", label="稀疏（真实）")
    ax.bar(xx + .2, p_vir, width=.4, color="#F58518", label="稠密（真实+虚拟）")
    ax.set_xticks(xx)
    ax.set_xticklabels([c[:11] for c in cats], rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("占比"); ax.set_title(f"细胞类型构成对比（TV={ct_tv:.3f}）", fontsize=10)
    ax.legend(fontsize=8); ax.grid(alpha=.25, axis="y")

    fig.suptitle("任务D：MERFISH 论文协议复现（5 真实切片，每对插 3 虚拟层，seed=42）",
                 fontsize=12)
    fig.tight_layout()
    save_png("merfish_dense_eval.png", fig)

    payload = {
        "experiment": "D_MERFISH_PAPER_PROTOCOL",
        "source": "SpatialZ (Nature Methods 2025 / bioRxiv 2024.12.06.627127) Fig.3",
        "seed": 42, "nb_iter_max": NB_ITER, "syn_mode": "fast",
        "protocol": {
            "n_real_sections": len(sections),
            "virtual_per_gap": N_VIRT_PER_GAP,
            "n_virtual_sections": len(virtuals),
            "note": "论文：每两张相邻真实切片之间生成 3 个虚拟切片",
        },
        "paper_values": {
            "fig3j_ARI": {"STAGATE": "~0.48 -> ~0.52", "BINARY": "~0.44 -> ~0.53"},
            "fig3k_NMI": {"STAGATE": "~0.58 -> ~0.60", "BINARY": "~0.57 -> ~0.61"},
            "note": "数值由论文 Fig.3j/k 图中读出（箱线图，无正文数字）；"
                    "论文用 STAGATE 与 BINARY 两种空间聚类器",
        },
        "deviation": {
            "clusterer": "KMeans(n_clusters=8, seed=42) on 50-D PCA",
            "reason": "venv 内未安装 STAGATE / BINARY，且本任务不允许安装新包；"
                      "两臂使用同一聚类器，相对比较仍成立",
        },
        "cells": {
            "sparse_arm": int(X_s.shape[0]),
            "dense_arm": int(X_d.shape[0]),
            "virtual_added": int(sum(a.n_obs for a in virtuals)),
        },
        "composition": {
            "cell_type": {"tv_distance": ct_tv, "cosine_similarity": ct_cos,
                          "order": ct_order},
            "domain": {"tv_distance": dm_tv, "cosine_similarity": dm_cos,
                       "order": dm_order},
        },
        "downstream": {
            "sparse": {"ARI": ari_s, "NMI": nmi_s},
            "dense": {"ARI": ari_d, "NMI": nmi_d},
            "delta": {"ARI": ari_d - ari_s, "NMI": nmi_d - nmi_s},
        },
        "runtime_sec": round(t.total(), 1),
    }
    save_json("merfish_dense_eval.json", payload)

    print("\n==== 任务D 汇总（论文 vs 我们）====")
    print(f"  细胞数:稀疏 {X_s.shape[0]} -> 稠密 {X_d.shape[0]}（+{payload['cells']['virtual_added']}）")
    print(f"  ARI: 稀疏 {ari_s:.4f} -> 稠密 {ari_d:.4f}（Δ{ari_d - ari_s:+.4f}）"
          f"   论文约 0.48->0.52(STAGATE) / 0.44->0.53(BINARY)")
    print(f"  NMI: 稀疏 {nmi_s:.4f} -> 稠密 {nmi_d:.4f}（Δ{nmi_d - nmi_s:+.4f}）"
          f"   论文约 0.58->0.60 / 0.57->0.61")
    print(f"  总耗时 {t.total():.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())