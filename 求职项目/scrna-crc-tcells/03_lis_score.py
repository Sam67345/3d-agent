#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_lis_score.py
================
任务：计算线粒体 DNA（mtDNA）变异的 LIS 评分（Lineage Informative Score，
谱系信息评分），筛选可信赖的"谱系标记变异"，用于后续把 mtDNA 突变
当作天然条形码，追踪 T 细胞克隆扩增。

公式出处：
    LIS(variant) = Mean(VAF) / (1 + Var(VAF))
    参考 Wang et al., Genome Biology 2025 关于单细胞 mtDNA 谱系标记的
    评分定义。直觉解释：
      - Mean(VAF) 高：该变异在很多细胞里真实存在，而不是某个细胞的噪音；
      - Var(VAF) 小：该变异在携带细胞中 VAF 稳定（同质性高），
        符合"一次突变事件被克隆遗传"的预期；
      - 分母 +1 防止方差为 0 时除零，并把分数压缩到 (0, 1) 区间便于解释。
    LIS >= 0.6 视为可信谱系标记（本文采用文献中的常用阈值）。

输入：
    mtDNA 变异 VAF 矩阵 CSV。行 = 变异（第一列为变异 ID），列 = 细胞，
    值为 VAF（0~1，缺失表示该细胞未检测到该变异，按 0 处理）。

输出：
    <输入文件名>_lis.csv，包含每个变异的 mean_vaf / var_vaf / LIS /
    is_lineage_marker（LIS >= 0.6）。

用法：
    python 03_lis_score.py vaf_matrix.csv            # 正式分析
    python 03_lis_score.py --demo                    # 生成模拟数据并自测
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# LIS 阈值：文献常用 0.6，作为模块常量便于修改与引用
LIS_THRESHOLD = 0.6


def compute_lis(vaf_df: pd.DataFrame) -> pd.DataFrame:
    """计算每个变异的 LIS 评分。

    参数
    ----
    vaf_df : pd.DataFrame
        行 = 变异，列 = 细胞，值 = VAF。缺失值应已在调用前填 0。

    返回
    ----
    pd.DataFrame
        含 mean_vaf, var_vaf, LIS, is_lineage_marker 四列，按 LIS 降序。
    """
    # 沿列方向（axis=1）对每个变异统计所有细胞的 VAF 均值与方差
    mean_vaf = vaf_df.mean(axis=1)
    # ddof=1：样本方差（除以 n-1），与文献定义一致；单细胞时用 ddof=0 防 NaN
    var_vaf = vaf_df.var(axis=1, ddof=1).fillna(0.0)

    # LIS 公式：Mean / (1 + Var)。numpy 向量化计算，速度快且避免逐行循环
    lis = mean_vaf / (1.0 + var_vaf)

    result = pd.DataFrame({
        "variant": vaf_df.index,
        "mean_vaf": mean_vaf.values,
        "var_vaf": var_vaf.values,
        "LIS": lis.values,
        # LIS >= 0.6 判定为可信谱系标记
        "is_lineage_marker": lis.values >= LIS_THRESHOLD,
    }).sort_values("LIS", ascending=False).reset_index(drop=True)
    return result


def load_vaf_matrix(csv_path: str) -> pd.DataFrame:
    """读取 VAF 矩阵 CSV 并做基本校验。"""
    df = pd.read_csv(csv_path)
    if df.shape[1] < 2:
        raise ValueError("VAF 矩阵至少需要 1 列变异 ID + 1 列细胞数据")
    df = df.set_index(df.columns[0])           # 第一列 = 变异 ID
    df = df.apply(pd.to_numeric, errors="coerce")  # 非数值转 NaN
    df = df.fillna(0.0)                        # 未检测到 = VAF 0
    if (df.values < 0).any() or (df.values > 1).any():
        raise ValueError("VAF 值必须在 [0, 1] 区间内，请检查输入矩阵")
    return df


def make_demo_matrix(output_csv: str, n_variants: int = 12, n_cells: int = 60,
                     seed: int = 42) -> None:
    """生成一份小型模拟 VAF 矩阵，用于自测流程。

    模拟设计（对应真实生物学预期）：
      - 前 3 个变异：高 VAF、低方差的"真谱系标记"（模拟克隆奠基者突变）
      - 中间变异：低 VAF 散在噪音（模拟测序假阳性）
      - 其余：零星出现的稀有变异（LIS 应接近 0）
    """
    rng = np.random.default_rng(seed)
    cells = [f"cell_{i:03d}" for i in range(n_cells)]
    data = {}

    # 真谱系标记：绝大多数细胞共享高且稳定的 VAF（模拟克隆奠基者突变）。
    # 注意：LIS = Mean/(1+Var)，若变异只存在于一小簇细胞，大量 0 值会拉高
    # 方差、压低均值，LIS 难以超过 0.6——这正是 LIS 的设计意图：它奖励
    # "广泛存在且稳定"的奠基者突变，而非小亚克隆的私有突变。
    for v in range(3):
        vaf = np.zeros(n_cells)
        carrier = rng.choice(n_cells, size=int(n_cells * 0.9), replace=False)
        vaf[carrier] = rng.normal(loc=0.9, scale=0.05,
                                  size=len(carrier)).clip(0, 1)
        data[f"MT:{576+v*10}:G:A"] = vaf

    # 噪音变异：随机细胞出现低 VAF
    for v in range(3, 8):
        vaf = np.zeros(n_cells)
        idx = rng.choice(n_cells, size=5, replace=False)
        vaf[idx] = rng.uniform(0.01, 0.08, size=5)
        data[f"MT:{2000+v*37}:C:T"] = vaf

    # 稀有变异：只在 1~2 个细胞出现
    for v in range(8, n_variants):
        vaf = np.zeros(n_cells)
        idx = rng.choice(n_cells, size=2, replace=False)
        vaf[idx] = rng.uniform(0.2, 0.5, size=2)
        data[f"MT:{9000+v*53}:T:C"] = vaf

    demo_df = pd.DataFrame(data, index=cells).T
    demo_df.index.name = "variant"
    demo_df.to_csv(output_csv)
    print(f"[demo] 模拟 VAF 矩阵已写入 {output_csv} "
          f"({demo_df.shape[0]} 变异 x {demo_df.shape[1]} 细胞)")


def main():
    parser = argparse.ArgumentParser(
        description="计算 mtDNA 变异的 LIS 谱系信息评分（Wang et al., Genome Biology 2025）")
    parser.add_argument("input", nargs="?",
                        help="VAF 矩阵 CSV（行=变异，列=细胞）")
    parser.add_argument("--demo", action="store_true",
                        help="生成模拟数据 demo_vaf_matrix.csv 并跑通全流程自测")
    parser.add_argument("-o", "--output", default=None,
                        help="输出 CSV 路径（默认：<输入文件名>_lis.csv）")
    args = parser.parse_args()

    if args.demo:
        demo_csv = "demo_vaf_matrix.csv"
        make_demo_matrix(demo_csv)
        input_csv, output_csv = demo_csv, "demo_vaf_matrix_lis.csv"
    else:
        if not args.input:
            parser.error("请提供 VAF 矩阵 CSV 路径，或使用 --demo 运行自测")
        input_csv = args.input
        output_csv = args.output or str(Path(input_csv).with_suffix("")) + "_lis.csv"

    vaf_df = load_vaf_matrix(input_csv)
    print(f"读入 VAF 矩阵：{vaf_df.shape[0]} 个变异 x {vaf_df.shape[1]} 个细胞")

    result = compute_lis(vaf_df)
    result.to_csv(output_csv, index=False)

    n_marker = int(result["is_lineage_marker"].sum())
    print(f"LIS 评分完成 -> {output_csv}")
    print(f"可信谱系标记（LIS >= {LIS_THRESHOLD}）：{n_marker} / {len(result)} 个变异")
    print("\nTop 5 变异预览：")
    print(result.head(5).to_string(index=False))

    if args.demo:
        # 自测断言：我们设计的 3 个"真谱系标记"必须全部被识别出来
        assert n_marker >= 3, "demo 自测失败：真谱系标记未被正确识别"
        assert result.head(3)["is_lineage_marker"].all(), \
            "demo 自测失败：Top3 应全为谱系标记"
        print("\n[demo] 自测通过：模拟的 3 个克隆标记均被正确识别。")


if __name__ == "__main__":
    sys.exit(main())
