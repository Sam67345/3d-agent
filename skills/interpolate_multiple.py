"""技能实现：interpolate_multiple —— N 张切片的相邻对之间各插若干层虚拟切片。

M6.3：对应 SpatialZ 源码 Generate_multiple_slices(adata_list, num_sim_list, ...) 的技能封装。
注意：SpatialZ.py 里的 Generate_multiple_slices 在 anndata 0.13 下不可用——其末尾调用
AnnData.concatenate（0.10 起已移除）且自管理写文件；因此本技能按其确切语义复现：
对每对相邻切片 (i, i+1)，alpha = j/(k_i+1)（j=1..k_i，升序；与源码 alpha=1-j/(k_i+1)
是同一集合），逐层调用 M2 已验证的 gen_one（语义等价于源码逐对调 Generate_spatialz）。

供 agent 通过技能注册表调用；数据对象参数 adata_list 由 executor 从 session 注入。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m2_interpolate import OUT_DIR, gen_one  # noqa: E402
sys.path.insert(0, str(ROOT / "skills"))
from interpolate import default_mode_guard  # noqa: E402


def run(adata_list, n_layers, nb_iter=1000, syn_mode="fast", out_dir=None,
        confirm_default=False, ids=None):
    """对 N 张切片的每对相邻切片生成均匀分布的虚拟层。

    Parameters
    ----------
    adata_list : list[AnnData]
        load_slices 输出的 N 张预处理切片（同一基因面板、dense float32），
        按物理顺序排列（N >= 2）。
    n_layers : int | list[int]
        每对之间插入的层数。int 时对全部 N-1 对用同一层数；
        list 时长度必须为 N-1，逐对指定。每层 alpha = j/(k+1)（j=1..k）。
    nb_iter : int
        OT 重心梯度下降迭代数，默认 1000（CPU 下约 106 s/层）。
    syn_mode : str
        'fast'（默认，10-NN 同类型均值）或 'default'（仅适合小基因面板）。
    out_dir : str | Path | None
        输出目录，默认项目 output/。
    confirm_default : bool
        确认在 > 5000 基因面板上使用 syn_mode='default'（预计需数天）。
    ids : list[str] | None
        各切片标识（用于 slice_id 与文件名），缺省 slice1..sliceN。

    Returns
    -------
    dict（mode="multi_pair"）:
        virtual_slices : list[AnnData]  全部虚拟层（按对顺序、对内 alpha 升序）
        paths : list[str]               各层 h5ad 路径（与 virtual_slices 对齐）
        alphas : list[float]            各层 alpha
        pair_ids : list[str]            各层所属的切片对标识 "{id1}-{id2}"
        n_layers : int | list[int]      实际使用的层数配置
        total_layers : int
        elapsed_sec : float             总耗时
    dict（mode="guard"）：面板 > 5000 基因 + syn_mode='default' 且未确认，未生成数据
    """
    adata_list = list(adata_list)
    if len(adata_list) < 2:
        raise ValueError(f"interpolate_multiple 需要至少 2 张切片，收到 {len(adata_list)}")

    if syn_mode not in ("fast", "default"):
        raise ValueError(f"syn_mode 必须是 'fast' 或 'default'，收到 {syn_mode}")
    if nb_iter < 1:
        raise ValueError("nb_iter 必须 >= 1")

    n_pairs = len(adata_list) - 1
    if isinstance(n_layers, int) and not isinstance(n_layers, bool):
        k_list = [n_layers] * n_pairs
    else:
        k_list = list(n_layers)
        if len(k_list) != n_pairs:
            raise ValueError(f"n_layers 为列表时长度必须等于切片对数 {n_pairs}，收到 {len(k_list)}")
        if any((not isinstance(k, int)) or isinstance(k, bool) or k < 1 for k in k_list):
            raise ValueError(f"n_layers 每项必须是 >= 1 的整数，收到 {k_list}")

    guard = default_mode_guard(adata_list[0].n_vars, syn_mode, confirm_default)
    if guard is not None:
        return guard

    ids = [str(s) for s in ids] if ids else [f"slice{i+1}" for i in range(len(adata_list))]
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    sims, paths, alphas, pair_ids = [], [], [], []
    t0 = time.time()
    for i in range(n_pairs):
        a1, a2 = adata_list[i], adata_list[i + 1]
        id1, id2 = ids[i], ids[i + 1]
        for j in range(1, k_list[i] + 1):
            alpha = j / (k_list[i] + 1)
            sid = f"{id1}-{id2}-alpha{alpha:.3f}"
            sim = gen_one(a1, a2, alpha, nb_iter, syn_mode=syn_mode,
                          adata1_id=id1, adata2_id=id2, slice_id=sid)
            fpath = out / f"{sid}.h5ad"
            sim.write_h5ad(fpath)
            sims.append(sim)
            paths.append(str(fpath))
            alphas.append(alpha)
            pair_ids.append(f"{id1}-{id2}")
            print(f"Completed {sid} generated and saved!")

    return {"mode": "multi_pair", "virtual_slices": sims, "paths": paths,
            "alphas": alphas, "pair_ids": pair_ids, "n_layers": n_layers,
            "total_layers": len(sims), "elapsed_sec": round(time.time() - t0, 1)}
