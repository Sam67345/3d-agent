"""M10 验证实验公共库：确定性设置、指标计算、结果落盘。

铁律：不改agent/ skills/ 等任何现有文件，只在 experiments/ 内新增。
所有随机过程统一 seed=42，保证结果可复现（论文第四章需要）。
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output" / "validation"
SPATIALZ_DIR = ROOT / "spatialz_src" / "SpatialZ_code"

# 允许的内存上限（GB），用于粗粒度防御（任务要求 16GB）
MEM_LIMIT_GB = 16


def set_seed(seed: int = SEED) -> np.random.Generator:
    """固定所有随机源，返回 np随机数生成器。"""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
        np.random.seed(seed)
    except ImportError:
        pass
    return np.random.default_rng(seed)


def add_spatialz_to_path() -> None:
    """把项目根目录加入 sys.path，从而 import 根目录那份**已修补**的 SpatialZ.py。

    注意：不要用 spatialz_src/SpatialZ_code/SpatialZ.py —— 那份是上游原版，
    第 127 行还带着旧版 anndata 的 AnnData(..., dtype=) 写法，在 anndata 0.13 下直接
    TypeError。项目根目录的副本只改了这一处（并补了 import anndata as ad），
    正是 skills/m2_interpolate.py 用的那一份，保证实验与线上行为一致。
    """
    p = str(ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------- 指标 ----------------

def per_cell_correlation(real: np.ndarray, syn: np.ndarray,
                         method: str = "pearson") -> np.ndarray:
    """配对后的逐细胞表达相关。

    real / syn: (n_cells, n_genes) 已按同一顺序配对好。
    注意：Pearson 逐行计算等价于对去均值后的向量做内积归一；
    Spearman 先把每行换成秩再算Pearson。
    """
    real = np.asarray(real, dtype=np.float64)
    syn = np.asarray(syn, dtype=np.float64)
    if method == "spearman":
        from scipy.stats import rankdata
        real = rankdata(real, axis=1)
        syn = rankdata(syn, axis=1)
    real = real - real.mean(axis=1, keepdims=True)
    syn = syn - syn.mean(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        num = (real * syn).sum(axis=1)
        den = np.sqrt((real * real).sum(axis=1) * (syn * syn).sum(axis=1))
        return np.where(den > 0, num / den, np.nan)


def composition_distance(labels_true, labels_syn, order: list | None = None):
    """细胞类型构成差异：总变差距离 TV + 余弦相似度。

    TV = 0.5 * Σ|p_true - p_syn|（0~1，越小越像）
    cosine = Σp_true*p_syn / (|p_true||p_syn|)（越接近 1 越像，用于和外部文献对标）
    """
    labels_true = np.asarray(labels_true)
    labels_syn = np.asarray(labels_syn)
    if order is None:
        order = sorted(set(labels_true.tolist()) | set(labels_syn.tolist()))
    p_true = np.array([(labels_true == c).sum() for c in order], dtype=float)
    p_syn = np.array([(labels_syn == c).sum() for c in order], dtype=float)
    p_true = p_true / max(p_true.sum(), 1)
    p_syn = p_syn / max(p_syn.sum(), 1)
    tv = 0.5 * float(np.abs(p_true - p_syn).sum())
    cos = float((p_true @ p_syn) / (np.linalg.norm(p_true) * np.linalg.norm(p_syn) + 1e-12))
    return tv, cos, [str(c) for c in order], p_true.tolist(), p_syn.tolist()


def morans_i_gene_wise(expr: np.ndarray, coords: np.ndarray, k: int = 6):
    """逐基因 Moran's I（空间自相关），用于和外部文献的"基因级 Moran's I 相关"对标。

    权重用稀疏矩阵实现：5000 细胞时稠密 W 要200MB×2，稀疏则几MB，避免撞内存上限。
    """
    from scipy import sparse as sp
    from sklearn.neighbors import NearestNeighbors
    expr = np.asarray(expr, dtype=np.float64)
    n, g = expr.shape
    nn = NearestNeighbors(n_neighbors=k + 1).fit(coords)
    _, idx = nn.kneighbors(coords)
    rows = np.repeat(np.arange(n), k + 1)
    cols = idx.ravel()
    vals = np.ones(rows.shape[0])
    W = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
    W.setdiag(0)
    W.eliminate_zeros()
    # 行归一化
    rs = np.asarray(W.sum(axis=1)).ravel()
    rs[rs == 0] = 1.0
    W = sp.diags(1.0 / rs) @ W

    z = expr - expr.mean(axis=0, keepdims=True)
    out = np.empty(g)
    for j in range(g):
        zj = z[:, j]
        denom = (zj ** 2).sum()
        out[j] = (g / n) * float(zj @ (W @ zj)) / denom if denom > 0 else np.nan
    return out


# ---------------- 落盘 ----------------

def ensure_out_dir() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR


def save_json(name: str, payload: dict) -> Path:
    path = ensure_out_dir() / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[save] {path}", flush=True)
    return path


def save_png(name: str, fig) -> Path:
    path = ensure_out_dir() / name
    fig.savefig(path, dpi=160, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    print(f"[save] {path}", flush=True)
    return path


class Timer:
    """分段计时，汇报里要写每个任务运行时长。"""

    def __init__(self, name: str):
        self.name = name
        self.t0 = time.time()
        print(f"=== {name} 开始 ===", flush=True)

    def lap(self, msg: str) -> float:
        now = time.time()
        print(f"  [{msg}] 用时 {now - self.t0:.1f}s", flush=True)
        return now - self.t0

    def total(self) -> float:
        return time.time() - self.t0


def ensure_font_cn():
    """matplotlib 中文字体（找不到就退回默认，不影响出图）。"""
    import matplotlib
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"]
    matplotlib.rcParams["axes.unicode_minus"] = False