"""M1: 加载 DLPFC 相邻切片并打印数据概况。

验收标准：能加载 DLPFC 切片并打印数据概况（spot 数、基因数、坐标范围）。
"""

from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import scanpy as sc

DATA_DIR = Path(__file__).parent / "data"

# spatialLIBD DLPFC：151673 与 151674 来自同一供体的相邻连续切片
SAMPLES = ["151673", "151674"]


def load_sample(sample_id: str) -> ad.AnnData:
    """加载单张切片 h5ad。

    分层注释直接用 h5ad 自带的 obs['Region'] 列（经核对与 spatialLIBD
    官方 truth.txt 逐层计数完全一致），无需额外注释文件。
    """
    fpath = DATA_DIR / f"{sample_id}_filtered_feature_bc_matrix.h5ad"
    if not fpath.exists():
        raise FileNotFoundError(f"缺少数据文件: {fpath}")
    adata = sc.read_h5ad(fpath)
    if "Region" not in adata.obs:
        raise KeyError(f"{sample_id} 缺少 obs['Region'] 分层注释列")
    # 丢弃无注释（NaN，位于切片边缘）的 spot
    adata = adata[adata.obs["Region"].notna()].copy()
    adata.obs["layer"] = adata.obs["Region"].astype(str)
    return adata


def print_overview(adata: ad.AnnData, sample_id: str) -> None:
    print(f"\n{'='*60}")
    print(f"切片 {sample_id}")
    print(f"{'='*60}")
    print(f"spot 数 (obs):            {adata.n_obs}")
    print(f"基因数 (var):             {adata.n_vars}")
    print(f"表达矩阵:                 {type(adata.X).__name__}, "
          f"非零比例 {np.mean(adata.X.toarray() != 0) if hasattr(adata.X, 'toarray') else np.mean(adata.X != 0):.2%}")

    # 空间坐标：Visium 标准存在 obsm['spatial']（像素坐标，x=列, y=行）
    coord_keys = list(adata.obsm_keys())
    print(f"obsm 键:                  {coord_keys}")
    if "spatial" in adata.obsm:
        xy = adata.obsm["spatial"]
        print(f"坐标范围 x (col):         [{xy[:, 0].min():.0f}, {xy[:, 0].max():.0f}]")
        print(f"坐标范围 y (row):         [{xy[:, 1].min():.0f}, {xy[:, 1].max():.0f}]")
    else:
        # 退化方案：部分导出文件把坐标放在 obs 的 array_row / array_col
        for c in ("array_col", "array_row", "pxl_col_in_fullres", "pxl_row_in_fullres"):
            if c in adata.obs:
                v = adata.obs[c]
                print(f"obs['{c}'] 范围:     [{v.min():.0f}, {v.max():.0f}]")

    if "layer" in adata.obs:
        print("皮层分层注释 (layer):")
        print(adata.obs["layer"].value_counts().sort_index().to_string())


def main() -> None:
    sc.settings.verbosity = 1
    for sid in SAMPLES:
        adata = load_sample(sid)
        print_overview(adata, sid)


if __name__ == "__main__":
    main()
