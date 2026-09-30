"""技能实现：validate —— 对真实+虚拟切片序列执行三项质量检查。

供 agent 通过技能注册表调用。逻辑全部复用 m2_validate.run_validation。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m2_validate import run_validation  # noqa: E402


def run(order, names):
    """执行三项验证并返回结构化结果。

    Parameters
    ----------
    order : list[AnnData]
        按 z 轴顺序排列的切片（真实与虚拟混合），如
        [151674, a=0.167, a=0.333, a=0.5, a=0.667, a=0.833, 151673]。
    names : list[str]
        与 order 一一对应的切片名。

    Returns
    -------
    dict:
        layer_fractions : dict[str, dict[str, float]]
            每个分层在各切片中的构成比例（验证 1：应随 alpha 平滑过渡）。
        adjacent_spearman : list[dict]
            相邻切片全基因平均表达 Spearman 相关
            [{pair: [name_i, name_j], rho, pvalue}, ...]（验证 2）。
        coord_ranges : dict[str, dict]
            各切片坐标范围与 spot 数（验证 3：虚拟切片应落在真实切片之间）。
    """
    if len(order) < 3:
        raise ValueError("validate 至少需要 3 层切片（真实-虚拟-真实）")
    if len(order) != len(names):
        raise ValueError("order 与 names 长度不一致")

    adatas = list(order)
    for i, adata in enumerate(adatas):
        if "Region" not in adata.obs:
            raise KeyError(f"切片 {names[i]} 缺少 obs['Region'] 分层注释")
        if "spatial" not in adata.obsm:
            raise KeyError(f"切片 {names[i]} 缺少 obsm['spatial'] 坐标")

    return run_validation(adatas, list(names))
