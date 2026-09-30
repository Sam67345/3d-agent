"""M2: 手工调用 SpatialZ 完成 2 张 DLPFC 切片间插值，输出虚拟切片。

不用 agent，直接脚本调用 SpatialZ（源码文件 SpatialZ.py 位于项目根目录）。

SpatialZ 单步流程（阅读源码 SpatialZ.py 确认）：
  1. 空间坐标生成：从两张真实切片均匀抽样初始化，Sliced Wasserstein 重心梯度下降
     （ot.sliced_wasserstein_distance, torch 张量，CPU 可跑）——即"对齐+插值"核心
  2. 细胞类型判定：新坐标对两张切片分别 kNN，多数投票定类型（cell_type_key）
  3. 属性转移（add_obs_list）
  4. 基因表达合成：
     - syn_mode='default'：MENDER 微环境度量 + 逐基因加权抽样（merfish 155 基因设计，
       对 Visium 3.3 万基因会退化为上亿次 Python 循环，不可用）
     - syn_mode='fast'：10-NN 同类型均值（向量化，适用大基因面板）→ 本脚本采用

用法：
  # 烟测（小数据快速验证 API 兼容性）：
  python m2_interpolate.py --smoke
  # 生成指定 alpha 的一层虚拟切片：
  python m2_interpolate.py --alpha 0.5
  # 生成 5 层并合并保存：
  python m2_interpolate.py --all
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import scanpy as sc

sys.path.insert(0, str(Path(__file__).parent))
from SpatialZ import Generate_spatialz  # noqa: E402
from m1_load_data import SAMPLES, load_sample  # noqa: E402

OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(exist_ok=True)

N_HVG = 2000          # 每切片选 top HVG，取并集作为公共基因面板
SEED = 42


def preprocess(adata, n_hvg=N_HVG, smoke=False):
    """归一化 + log1p + 选 HVG；返回 dense float32 表达矩阵的副本。"""
    adata = adata.copy()
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    if smoke:
        sc.pp.subsample(adata, n_obs=400, random_state=SEED, copy=False)
        sc.pp.highly_variable_genes(adata, n_top_genes=300, flavor="seurat")
    else:
        sc.pp.highly_variable_genes(adata, n_top_genes=n_hvg, flavor="seurat")
    return adata


def make_panel(adata1, adata2, smoke=False):
    """取两张切片 HVG 并集，将 X 切成 dense float32 公共面板。"""
    if smoke:
        panel = adata1.var_names[:300]  # 烟测直接取前 300 基因
    else:
        hvg1 = set(adata1.var_names[adata1.var["highly_variable"]])
        hvg2 = set(adata2.var_names[adata2.var["highly_variable"]])
        panel = sorted(hvg1 | hvg2)
    a1 = adata1[:, panel].copy()
    a2 = adata2[:, panel].copy()
    a1.X = np.asarray(a1.X.todense() if hasattr(a1.X, "todense") else a1.X, dtype=np.float32)
    a2.X = np.asarray(a2.X.todense() if hasattr(a2.X, "todense") else a2.X, dtype=np.float32)
    a1.obs["Region"] = a1.obs["Region"].astype(str)
    a2.obs["Region"] = a2.obs["Region"].astype(str)
    return a1, a2


def gen_one(a1, a2, alpha, nb_iter_max, smoke=False, syn_mode="fast",
            adata1_id=None, adata2_id=None, slice_id=None):
    """在 alpha 位置生成一层虚拟切片（alpha 越大越靠近 a1）。

    syn_mode: 'fast'（10-NN 同类型均值，适用大基因面板，默认）或
              'default'（MENDER 微环境 + 逐基因抽样，仅适合小基因面板）

    adata1_id / adata2_id / slice_id：可选自定义标识（M6.3 interpolate_multiple
    用）；缺省保持 M2 行为（SAMPLES id + virtual_alpha{alpha} 命名）。
    """
    np.random.seed(SEED)
    sim = Generate_spatialz(
        a1, a2,
        adata1_id=adata1_id or SAMPLES[0], adata2_id=adata2_id or SAMPLES[1],
        alpha=alpha,
        device="cpu",                      # 硬性约束：CPU 可跑
        nb_iter_max=nb_iter_max,
        seed=SEED,
        cell_type_key="Region",            # Visium spot 无单细胞类型，用皮层分层注释
        syn_mode=syn_mode,
        k_sam=50,
        add_obs_list=["layer"],
        verbose=True,
    )
    sim.obs["slice_id"] = slice_id or f"virtual_alpha{alpha:.3f}"
    sim.obs["data_type"] = "synthetic"
    return sim


def report(sim, alpha):
    xy = sim.obsm["spatial"]
    print(f"\n--- 虚拟切片 alpha={alpha:.3f} ---")
    print(f"spot 数: {sim.n_obs}, 基因数: {sim.n_vars}")
    print(f"坐标 x 范围: [{xy[:,0].min():.0f}, {xy[:,0].max():.0f}], "
          f"y 范围: [{xy[:,1].min():.0f}, {xy[:,1].max():.0f}]")
    print("分层构成 (Region):")
    print(sim.obs["Region"].value_counts().sort_index().to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="小样本 API 烟测")
    ap.add_argument("--alpha", type=float, default=None, help="只生成指定 alpha 的一层")
    ap.add_argument("--all", action="store_true", help="生成 5 层并合并保存")
    ap.add_argument("--nb-iter", type=int, default=None)
    args = ap.parse_args()

    smoke = args.smoke
    nb_iter = args.nb_iter or (50 if smoke else 1000)

    t0 = time.time()
    a1_raw, a2_raw = load_sample(SAMPLES[0]), load_sample(SAMPLES[1])
    a1, a2 = make_panel(preprocess(a1_raw, smoke=smoke), preprocess(a2_raw, smoke=smoke))
    print(f"预处理完成: {a1.n_obs}x{a1.n_vars} / {a2.n_obs}x{a2.n_vars}, "
          f"耗时 {time.time()-t0:.1f}s")

    alphas = [1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6]
    if args.alpha is not None:
        alphas = [args.alpha]

    sims = []
    for alpha in alphas:
        t = time.time()
        sim = gen_one(a1, a2, alpha, nb_iter, smoke=smoke)
        report(sim, alpha)
        print(f"本层耗时 {time.time()-t:.1f}s")
        sims.append(sim)
        fpath = OUT_DIR / f"virtual_alpha{alpha:.3f}.h5ad"
        sim.write_h5ad(fpath)
        print(f"已保存 {fpath}")

    if args.all and not smoke:
        import anndata as ad
        for a, sid in ((a1, SAMPLES[0]), (a2, SAMPLES[1])):
            a.obs["slice_id"] = sid
            a.obs["data_type"] = "real"
        dense = ad.concat([a1, a2] + sims, join="outer", merge="same")
        dense.write_h5ad(OUT_DIR / "dlpfc_dense_5sim.h5ad")
        print(f"\n合并 dense 数据已保存: {OUT_DIR/'dlpfc_dense_5sim.h5ad'}")
        print(f"总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
