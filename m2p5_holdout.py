"""M2.5 抽稀重建验证（held-out reconstruction）：SpatialZ 论文核心验证。

实验设计：
  - 输入：同供体两张"间隔一张"的切片 151673 与 151675
  - 用 SpatialZ 在 alpha=0.5 生成虚拟中间层
  - 用真实的 151674（held-out，全程未参与插值）作为真值评估重建质量

流程与 m2_interpolate 完全一致：normalize_total + log1p + 两切片 HVG 并集面板
+ dense float32；复用 m1_load_data.load_sample / m2_interpolate.preprocess/make_panel。

用法：
  python m2p5_holdout.py            # 全流程（插值约 110 s）
  python m2p5_holdout.py --skip-interp   # 已有输出时只重建对象（调试用）
"""

import argparse
import sys
import time
from pathlib import Path

import anndata as ad
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from m1_load_data import load_sample  # noqa: E402
from m2_interpolate import OUT_DIR, SEED, gen_one, make_panel, preprocess  # noqa: E402

SID_A, SID_B, SID_HELD = "151673", "151675", "151674"
HOLD_OUT = Path(__file__).parent / "output" / "holdout"


def build_inputs():
    """加载三张切片并按 m2 相同流程预处理。

    Returns
    -------
    a_held : AnnData     # 151674（真值），与插值对同一基因面板
    sim : AnnData        # 虚拟中间层（alpha=0.5）
    """
    # 插值对：151673 / 151675（load_sample 内部丢弃 Region 为 NaN 的 spot）
    raw_a, raw_b = load_sample(SID_A), load_sample(SID_B)
    a, b = make_panel(preprocess(raw_a), preprocess(raw_b))
    print(f"插值对: {SID_A} {a.n_obs} spots, {SID_B} {b.n_obs} spots, "
          f"面板 {a.n_vars} 基因")

    # 真值：151674 同样预处理，再切到同一基因面板
    held_raw = load_sample(SID_HELD)
    held = preprocess(held_raw)
    held = held[:, a.var_names].copy()
    held.X = np.asarray(held.X.todense() if hasattr(held.X, "todense") else held.X,
                        dtype=np.float32)
    held.obs["Region"] = held.obs["Region"].astype(str)
    print(f"真值: {SID_HELD} {held.n_obs} spots（同一 {held.n_vars} 基因面板）")

    return a, b, held


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-interp", action="store_true")
    args = ap.parse_args()

    HOLD_OUT.mkdir(parents=True, exist_ok=True)
    sim_path = HOLD_OUT / "virtual_mid_151674.h5ad"
    held_path = HOLD_OUT / "held_151674_panel.h5ad"

    a, b, held = build_inputs()
    held.write_h5ad(held_path)

    if args.skip_interp and sim_path.exists():
        sim = ad.read_h5ad(sim_path)
        print(f"复用已有虚拟中间层: {sim_path} ({sim.n_obs} spots)")
    else:
        t0 = time.time()
        sim = gen_one(a, b, alpha=0.5, nb_iter_max=1000, syn_mode="fast")
        sim.obs["slice_id"] = "virtual_mid"
        sim.obs["data_type"] = "synthetic"
        print(f"虚拟中间层: {sim.n_obs} spots, 耗时 {time.time()-t0:.1f}s")
        sim.write_h5ad(sim_path)

    print(f"\n输出: {sim_path}\n      {held_path}")


if __name__ == "__main__":
    main()
