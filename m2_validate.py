"""M2 验证：合并真实+虚拟切片，检查插值合理性。

验证项：
  1. 分层构成应随 alpha 从 151674 的构成平滑过渡到 151673 的构成
  2. 相邻切片（真实-虚拟-真实）的全局基因表达均值 Spearman 相关应单调递减
  3. 坐标范围应落在两张真实切片之间

`run_validation` 为可导入函数（M3 validate 技能调用），main() 为命令行入口。
"""

from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from m1_load_data import SAMPLES, load_sample
from m2_interpolate import OUT_DIR, make_panel, preprocess

VIRTUAL_ALPHAS = [0.167, 0.333, 0.500, 0.667, 0.833]


def layer_fractions(obs):
    vc = obs["Region"].value_counts()
    return (vc / vc.sum()).sort_index()


def run_validation(order, names):
    """对按插值顺序排列的切片列表执行三项验证，返回结构化 dict。

    Parameters
    ----------
    order : list[AnnData]
        按 z 轴顺序排列的切片（真实与虚拟混合）。
    names : list[str]
        与 order 对应的切片名。

    Returns
    -------
    dict:
        layer_fractions : dict[str, dict[str, float]]   验证 1
        adjacent_spearman : list[dict]                  验证 2，含 pair/rho/pvalue
        coord_ranges : dict[str, dict]                  验证 3，含 x/y 范围与 spot 数
    """
    # ---- 验证 1：分层构成 ----
    frac_df = pd.DataFrame({n: layer_fractions(d.obs) for n, d in zip(names, order)})
    v1 = {layer: {n: float(frac_df.loc[layer, n]) for n in names}
          for layer in frac_df.index}

    # ---- 验证 2：相邻层平均表达 Spearman 相关 ----
    means = [np.asarray(d.X.mean(axis=0)).ravel() for d in order]
    v2 = []
    for i in range(len(means) - 1):
        rho, p = spearmanr(means[i], means[i + 1])
        v2.append({"pair": [names[i], names[i + 1]],
                   "rho": float(rho), "pvalue": float(p)})

    # ---- 验证 3：坐标范围 ----
    v3 = {}
    for n, d in zip(names, order):
        xy = d.obsm["spatial"]
        v3[n] = {"x_range": [float(xy[:, 0].min()), float(xy[:, 0].max())],
                 "y_range": [float(xy[:, 1].min()), float(xy[:, 1].max())],
                 "n_spots": int(d.n_obs)}

    return {"layer_fractions": v1, "adjacent_spearman": v2, "coord_ranges": v3}


def main():
    # 按 m2 相同方式预处理真实切片（保证基因面板一致）
    a1, a2 = make_panel(preprocess(load_sample(SAMPLES[0])),
                        preprocess(load_sample(SAMPLES[1])))
    a1.obs["slice_id"], a2.obs["slice_id"] = SAMPLES[0], SAMPLES[1]
    a1.obs["data_type"], a2.obs["data_type"] = "real", "real"

    sims = [ad.read_h5ad(OUT_DIR / f"virtual_alpha{al:.3f}.h5ad")
            for al in VIRTUAL_ALPHAS]

    # alpha 从小到大：靠近 151674 → 靠近 151673
    order = [a2] + sims + [a1]
    names = ["151674(real)"] + [f"a={al}" for al in VIRTUAL_ALPHAS] + ["151673(real)"]

    result = run_validation(order, names)

    print("=== 验证1: 分层构成比例（行=分层, 列=切片, 按 alpha 升序）===")
    print(pd.DataFrame(result["layer_fractions"]).round(3).to_string())

    print("\n=== 验证2: 相邻切片全基因平均表达 Spearman 相关 ===")
    for r in result["adjacent_spearman"]:
        print(f"{r['pair'][0]:>14} vs {r['pair'][1]:<14} rho={r['rho']:.4f}")

    print("\n=== 验证3: 各层坐标范围 ===")
    for n, cr in result["coord_ranges"].items():
        print(f"{n:>14}: x [{cr['x_range'][0]:.0f}, {cr['x_range'][1]:.0f}], "
              f"y [{cr['y_range'][0]:.0f}, {cr['y_range'][1]:.0f}], spots={cr['n_spots']}")

    # ---- 合并保存 dense 数据 ----
    dense = ad.concat([a1, a2] + sims, join="outer", merge="same")
    dense.obs["Region"] = dense.obs["Region"].astype(str)
    dense.obs["layer"] = dense.obs["Region"]
    out = OUT_DIR / "dlpfc_dense_5sim.h5ad"
    dense.write_h5ad(out)
    print(f"\n合并 dense 数据已保存: {out}  ({dense.n_obs} spots x {dense.n_vars} genes)")


if __name__ == "__main__":
    main()
