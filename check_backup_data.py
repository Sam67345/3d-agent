"""一次性校验脚本：检查备份数据（STARmap 3D 原始数据 + 五张 MERFISH 切片）完整性。

- STARmap: 原始 txt（28 基因 x 33598 细胞）转存为 h5ad 备用
- MERFISH: 五张 h5ad 逐张载入报告形状与关键 obs 列
"""
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc

ROOT = Path(__file__).resolve().parent

# ---------- STARmap 3D ----------
expr_path = ROOT / "data/starmap/STARmap_3D_data_expression.txt"
loc_path = ROOT / "data/starmap/STARmap_3D_data_cell_locations.txt"

# 首行为表头（cell_1..cell_33598），每行一个基因，行首列是基因名
expr_tbl = pd.read_csv(expr_path, sep="\t", header=0, index_col=0)
genes = expr_tbl.index.tolist()
counts = expr_tbl.to_numpy(dtype=np.float32)
locs = pd.read_csv(loc_path, sep="\t", header=None, names=["x", "y", "z"])

print(f"[STARmap] 基因数: {len(genes)} | 细胞数: {counts.shape[1]} | 坐标行数: {len(locs)}")
assert len(genes) == 28, f"基因数应为 28, 实际 {len(genes)}"
assert counts.shape[1] == len(locs) == 33598, "细胞数应为 33598"
assert locs["z"].min() == 6 and locs["z"].max() == 94, "z 范围应为 6-94"
n_zero = int((counts.sum(axis=0) == 0).sum())
print(f"[STARmap] z 范围: {locs['z'].min()}-{locs['z'].max()} | "
      f"零表达细胞: {n_zero}（SpatialZ 过滤后应剩 {33598 - n_zero}）")

adata = sc.AnnData(X=counts.T)  # cells x genes
adata.var_names = genes
adata.obs = locs.copy()
adata.obsm["spatial"] = locs[["x", "y"]].to_numpy(dtype=np.float32)
out_h5ad = ROOT / "data/starmap/starmap_3d_raw.h5ad"
adata.write_h5ad(out_h5ad)
print(f"[STARmap] 已转存 h5ad: {out_h5ad} | shape={adata.shape}")

# ---------- MERFISH 五张切片 ----------
print("\n[MERFISH] 五张切片检查:")
for f in sorted((ROOT / "data/merfish_five").glob("*.h5ad")):
    ad = sc.read_h5ad(f)
    obs_cols = [c for c in ("cell_class", "cell_subclass", "z", "slice_id") if c in ad.obs]
    zvals = sorted(ad.obs["z"].unique()) if "z" in ad.obs else None
    print(f"  {f.name}: shape={ad.shape} obs={obs_cols} "
          f"z={'min-max ' + str((min(zvals), max(zvals))) if zvals else 'N/A'} "
          f"spatial={'spatial' in ad.obsm}")
