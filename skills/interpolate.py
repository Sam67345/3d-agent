"""技能实现：interpolate —— 在两张切片之间生成虚拟切片（单层 alpha / 多层 n_layers 两种模式）。

供 agent 通过技能注册表调用。逻辑全部复用 m2_interpolate.gen_one（M2 已验证）；
多层模式按均匀 alpha 序列 i/(n_layers+1) 循环调用单层逻辑（与 m2 --all 同思路）。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m2_interpolate import OUT_DIR, gen_one  # noqa: E402

DEFAULT_ALPHA = 0.5
DEFAULT_MODE_GENE_LIMIT = 5000  # syn_mode='default' 在该基因数以上触发确认守卫


def default_mode_guard(n_genes, syn_mode, confirm_default):
    """M6.2：default 表达合成模式守卫。

    基因面板 > 5000 且 syn_mode='default' 且未确认时，返回警告 dict（不生成）；
    其余情况返回 None（放行）。default 模式是逐基因 Python 循环，大面板需数天。
    """
    if syn_mode == "default" and n_genes > DEFAULT_MODE_GENE_LIMIT and not confirm_default:
        return {
            "mode": "guard",
            "warning": (
                f"该面板 {n_genes} 个基因超过 {DEFAULT_MODE_GENE_LIMIT}，"
                "default 模式（MENDER 微环境+逐基因抽样）预计需数天，建议改用 syn_mode='fast'；"
                "确认要用 default 请传 confirm_default=true"),
            "n_genes": n_genes,
            "path": None,
        }
    return None


def _gen_one_checked(adata1, adata2, alpha, nb_iter, syn_mode, out):
    """单层生成 + 保存，返回 (sim, path, elapsed)。"""
    t0 = time.time()
    sim = gen_one(adata1, adata2, alpha, nb_iter, syn_mode=syn_mode)
    elapsed = time.time() - t0
    fpath = out / f"virtual_alpha{alpha:.3f}.h5ad"
    sim.write_h5ad(fpath)
    return sim, str(fpath), elapsed


def run(adata1, adata2, alpha=None, n_layers=None, nb_iter=1000,
        syn_mode="fast", out_dir=None, confirm_default=False):
    """在两张切片之间生成虚拟切片并保存。

    三种调用方式（优先级：显式 alpha > n_layers > 默认）：
    - 只给 alpha：在 alpha 位置生成单层；
    - 只给 n_layers：均匀生成 n_layers 层，alpha 序列为 i/(n_layers+1)；
    - 都不给：alpha=0.5 生成单层；
    - alpha 与 n_layers 同时给：按 alpha 单层生成，结果中注明 n_layers 被忽略。

    M6.2 守卫：基因面板 > 5000 且 syn_mode='default' 且未传 confirm_default 时，
    不生成、返回 {"mode": "guard", "warning": ...}。

    Parameters
    ----------
    adata1, adata2 : AnnData
        load_slices 技能输出的预处理切片对（同一基因面板、dense float32）。
    alpha : float | None
        插值位置，0 < alpha < 1；越大越靠近 adata1。缺省取 0.5。
    n_layers : int | None
        多层模式的层数，>= 1。提供时生成 alpha = i/(n_layers+1)（i=1..n_layers）
        的均匀虚拟层。
    nb_iter : int
        OT 重心梯度下降迭代数，默认 1000（CPU 下约 106 s/层）。
    syn_mode : str
        'fast'（默认，10-NN 同类型均值）或 'default'（仅适合小基因面板，见 README）。
    out_dir : str | Path | None
        输出目录，默认项目 output/。
    confirm_default : bool
        确认在 > 5000 基因面板上使用 syn_mode='default'（预计需数天）。

    Returns
    -------
    dict（mode="single"）:
        virtual_slice : AnnData  虚拟切片（obs 含 Region/layer/slice_id/data_type）
        path : str               保存的 h5ad 路径
        elapsed_sec : float      耗时
        alpha : float
        note : str | None        alpha 与 n_layers 同给时的提示，否则 None
    dict（mode="multi"）:
        virtual_slices : list[AnnData]  按 alpha 升序的全部虚拟切片
        paths : list[str]               对应的 h5ad 路径（与 alphas 对齐）
        alphas : list[float]            实际使用的 alpha 序列
        elapsed_sec : float             总耗时
        n_layers : int
    dict（mode="guard"）：M6.2 守卫触发，未生成任何数据
        warning : str
        n_genes : int
        path : None
    """
    if syn_mode not in ("fast", "default"):
        raise ValueError(f"syn_mode 必须是 'fast' 或 'default'，收到 {syn_mode}")
    if nb_iter < 1:
        raise ValueError("nb_iter 必须 >= 1")
    if n_layers is not None:
        if not isinstance(n_layers, int) or isinstance(n_layers, bool) or n_layers < 1:
            raise ValueError(f"n_layers 必须是 >= 1 的整数，收到 {n_layers}")

    guard = default_mode_guard(adata1.n_vars, syn_mode, confirm_default)
    if guard is not None:
        return guard

    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    if alpha is not None:
        if not (0 < alpha < 1):
            raise ValueError(f"alpha 必须在 (0,1) 内，收到 {alpha}")
        note = None
        if n_layers is not None:
            note = f"alpha 与 n_layers 同时提供，按 alpha={alpha} 单层生成，忽略 n_layers={n_layers}"
        sim, path, elapsed = _gen_one_checked(adata1, adata2, alpha, nb_iter, syn_mode, out)
        return {"mode": "single", "virtual_slice": sim, "path": path,
                "elapsed_sec": round(elapsed, 1), "alpha": alpha, "note": note}

    if n_layers is not None:
        alphas = [i / (n_layers + 1) for i in range(1, n_layers + 1)]
        sims, paths, total = [], [], 0.0
        for a in alphas:
            sim, path, elapsed = _gen_one_checked(adata1, adata2, a, nb_iter, syn_mode, out)
            sims.append(sim)
            paths.append(path)
            total += elapsed
        return {"mode": "multi", "virtual_slices": sims, "paths": paths,
                "alphas": alphas, "elapsed_sec": round(total, 1),
                "n_layers": n_layers}

    sim, path, elapsed = _gen_one_checked(adata1, adata2, DEFAULT_ALPHA, nb_iter, syn_mode, out)
    return {"mode": "single", "virtual_slice": sim, "path": path,
            "elapsed_sec": round(elapsed, 1), "alpha": DEFAULT_ALPHA, "note": None}
