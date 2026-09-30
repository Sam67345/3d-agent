"""技能实现：load_slices —— 载入 N 张 h5ad 切片并预处理（N >= 2）。

供 agent 通过技能注册表调用。逻辑复用 m1_load_data / m2_interpolate；
M6.1：切片基因数 < n_hvg 时跳过 HVG 筛选直接用全基因面板（meta 注明 hvg_skipped）；
M6.3：放开"恰好 2 张"限制，接受 N 张，按用户给定顺序排列。
"""

import sys
from pathlib import Path

import numpy as np
import scanpy as sc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m1_load_data import SAMPLES, load_sample  # noqa: E402
from m2_interpolate import preprocess  # noqa: E402


def _load_one_h5ad(fpath):
    """读取单个 h5ad 并做结构校验 + 丢弃无注释点（与 m1.load_sample 同规则）。

    分层注释列兼容两种命名：优先 obs['Region']（DLPFC），否则用 obs['cell_class']
    （MERFISH 等），统一复制一份到 obs['Region'] 供下游技能使用。
    """
    import anndata as ad
    fpath = Path(fpath)
    if not fpath.exists():
        raise FileNotFoundError(f"缺少切片文件: {fpath}")
    if fpath.suffix != ".h5ad":
        raise ValueError(f"文件必须是 .h5ad 格式: {fpath}")
    adata = ad.read_h5ad(fpath)
    if "spatial" not in adata.obsm:
        raise KeyError(f"{fpath.name} 缺少 obsm['spatial'] 空间坐标")
    if "Region" in adata.obs:
        type_col = "Region"
    elif "cell_class" in adata.obs:
        type_col = "cell_class"
        adata.obs["Region"] = adata.obs["cell_class"].astype(str)
    else:
        raise KeyError(f"{fpath.name} 缺少分层注释列（obs['Region'] 或 obs['cell_class']）")
    adata = adata[adata.obs["Region"].notna()].copy()
    adata.obs["Region"] = adata.obs["Region"].astype(str)
    adata.obs["layer"] = adata.obs["Region"]
    return adata


def _preprocess(adata, n_hvg, use_hvg):
    """归一化 + log1p；use_hvg 时选 HVG，否则全基因标记为面板（保持下游结构一致）。"""
    adata = adata.copy()
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    if use_hvg:
        sc.pp.highly_variable_genes(adata, n_top_genes=n_hvg, flavor="seurat")
    else:
        adata.var["highly_variable"] = True
    return adata


def _make_panel_multi(preps, use_hvg):
    """N 张切片切到同一 dense float32 公共面板。

    HVG 路径：全部切片 highly_variable 基因的并集（与 m2.make_panel 同规则，N=2 时等价）；
    全基因路径：全部切片 var_names 的交集（保证每张切片都含面板全部基因）。
    """
    if use_hvg:
        panel = sorted(set().union(*[
            set(p.var_names[p.var["highly_variable"]]) for p in preps]))
    else:
        panel = sorted(set.intersection(*[set(p.var_names) for p in preps]))
    out = []
    for p in preps:
        a = p[:, panel].copy()
        a.X = np.asarray(a.X.todense() if hasattr(a.X, "todense") else a.X,
                         dtype=np.float32)
        a.obs["Region"] = a.obs["Region"].astype(str)
        out.append(a)
    return out


def run(sample_ids=None, n_hvg=2000, data_dir=None, file_paths=None):
    """载入并预处理 N 张切片（N >= 2），返回插值就绪的切片列表。

    Parameters
    ----------
    sample_ids : list[str] | None
        切片 ID 列表，默认 SAMPLES（前两张）；按约定命名从 data_dir 查找。
        M6.3 起接受任意 >= 2 张，按给定顺序排列。
    n_hvg : int
        每切片选取的高变基因数（取 N 切片并集为公共面板）。
        M6.1：任一切片基因数 < n_hvg 时跳过 HVG 筛选，直接用全基因面板。
    data_dir : str | Path | None
        h5ad 数据目录，默认项目 data/。
    file_paths : list[str] | None
        N 个 h5ad 的绝对/相对路径，直接加载（优先于 sample_ids）。
        用于 MERFISH 等不符合约定命名的数据。

    Returns
    -------
    dict:
        adatas : list[AnnData]  按输入顺序的 N 张切片（归一化 + 面板 + dense float32）
        adata1, adata2          前两张（向后兼容老的两切片流程）
        meta : list[dict]       每张切片的 spot 数、基因数、坐标范围、hvg_skipped
    """
    if file_paths is not None:
        if isinstance(file_paths, (str, Path)):
            file_paths = [file_paths]
        paths = list(file_paths)
        if len(paths) < 2:
            raise ValueError("file_paths 需要至少 2 个 h5ad 路径")
        raws = [_load_one_h5ad(p) for p in paths]
        ids = [Path(p).stem for p in paths]
    else:
        if sample_ids is None:
            sample_ids = list(SAMPLES)
        if isinstance(sample_ids, (str, Path)):
            sample_ids = [sample_ids]
        ids = [str(s) for s in sample_ids]
        if len(ids) < 2:
            raise ValueError("load_slices 需要至少 2 个切片 ID")

        # data_dir 透传：通过临时改 m1 的模块级常量实现（load_sample 内部使用）
        from m1_load_data import DATA_DIR as _default_dir
        target_dir = Path(data_dir) if data_dir else _default_dir
        missing = [sid for sid in ids
                   if not (target_dir / f"{sid}_filtered_feature_bc_matrix.h5ad").exists()]
        if missing:
            raise FileNotFoundError(f"缺少切片文件: {missing} (目录 {target_dir})")

        import m1_load_data
        old_dir = m1_load_data.DATA_DIR
        m1_load_data.DATA_DIR = target_dir
        try:
            raws = [load_sample(sid) for sid in ids]
        finally:
            m1_load_data.DATA_DIR = old_dir

    # M6.1：小基因面板自适应
    hvg_skipped = any(r.n_vars < n_hvg for r in raws)
    preps = [_preprocess(r, n_hvg, use_hvg=not hvg_skipped) for r in raws]
    adatas = _make_panel_multi(preps, use_hvg=not hvg_skipped)

    def _meta(adata, sid):
        xy = adata.obsm["spatial"]
        return {
            "sample_id": sid,
            "n_spots": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "x_range": [float(xy[:, 0].min()), float(xy[:, 0].max())],
            "y_range": [float(xy[:, 1].min()), float(xy[:, 1].max())],
            "hvg_skipped": hvg_skipped,
        }

    return {"adatas": adatas, "adata1": adatas[0], "adata2": adatas[1],
            "meta": [_meta(a, sid) for a, sid in zip(adatas, ids)]}
