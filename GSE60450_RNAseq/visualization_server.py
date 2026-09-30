#!/usr/bin/env python3
# ============================================================================
# visualization.py
# GSE60450 RNA-seq 可视化脚本
# 输入：DESeq2 输出的 CSV 结果文件
# 输出：火山图（Volcano Plot）、热图（Heatmap）、Top 差异基因表格
#
# 运行方式：
#   python3 visualization.py
#
# 依赖：pandas, matplotlib, seaborn, numpy
#   pip install pandas matplotlib seaborn numpy
# ============================================================================

import os
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import seaborn as sns
from matplotlib.patches import Patch

# ---------------------------- 全局设置 ----------------------------
# 设置中文字体（如果你的系统没有 SimHei，可以改成 "DejaVu Sans" 等）
# 这里先用英文标签避免字体问题
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['font.size'] = 11
plt.rcParams['figure.dpi'] = 300

# 项目路径（服务器适配：指向集群项目目录）
PROJECT_DIR = os.path.expanduser("~/GSE60450_project")
RESULTS_DIR = os.path.join(PROJECT_DIR, "results")
FIG_DIR = os.path.join(RESULTS_DIR, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

# 配色方案
colors = {
    'up': '#E41A1C',      # 上调基因：红色
    'down': '#377EB8',    # 下调基因：蓝色
    'ns': '#CCCCCC',      # 不显著：灰色
    'highlight': '#FF7F00' # 高亮：橙色
}

# 阈值
PADJ_THRESH = 0.05       # FDR 阈值
LOG2FC_THRESH = 1.0      # log2FC 阈值（对应倍数变化 > 2 或 < 0.5）

print(f"📁 结果目录: {RESULTS_DIR}")
print(f"📁 图表目录: {FIG_DIR}")


# ============================================================================
# 函数 1：读取 DESeq2 结果并分类
# ============================================================================
def load_deseq2_results(csv_path):
    """
    读取 DESeq2 输出的 CSV 文件，并给每个基因标注分类标签。

    分类标准：
        - 'Up'    : padj < 0.05 且 log2FoldChange > 1  （显著上调）
        - 'Down'  : padj < 0.05 且 log2FoldChange < -1 （显著下调）
        - 'NS'    : 其余（不显著）

    参数:
        csv_path: DESeq2 results CSV 文件路径

    返回:
        DataFrame，新增 'sig' 列（分类标签）和 '-log10(padj)' 列
    """
    df = pd.read_csv(csv_path, index_col=0)

    # 清理数据：去掉 NA 值（padj 为 NA 表示独立过滤时被淘汰的基因）
    df = df.dropna(subset=['padj', 'log2FoldChange'])

    # 计算 -log10(padj)，用于火山图 Y 轴
    # 服务器适配：极显著基因的 padj 会下溢为 0，而 -log10(0) = 正无穷会让
    # matplotlib 设置坐标轴时报 "Axis limits cannot be NaN or Inf"。
    # 做法：把 padj 下限截断到 1e-300（对应 -log10 = 300），无穷大点画在图顶即可。
    df['neg_log10_padj'] = -np.log10(df['padj'].clip(lower=1e-300))

    # 分类：显著上调 / 显著下调 / 不显著
    conditions = [
        (df['padj'] < PADJ_THRESH) & (df['log2FoldChange'] > LOG2FC_THRESH),
        (df['padj'] < PADJ_THRESH) & (df['log2FoldChange'] < -LOG2FC_THRESH)
    ]
    choices = ['Up', 'Down']
    df['sig'] = np.select(conditions, choices, default='NS')

    # 统计
    n_up = (df['sig'] == 'Up').sum()
    n_down = (df['sig'] == 'Down').sum()
    n_total = len(df)
    print(f"  总基因数: {n_total}")
    print(f"  显著上调: {n_up}  |  显著下调: {n_down}")

    return df


# ============================================================================
# 函数 2：画火山图（Volcano Plot）
# ============================================================================
def plot_volcano(df, title, out_prefix):
    """
    画火山图：X 轴 = log2FoldChange，Y 轴 = -log10(padj)。

    火山图是差异表达分析最经典的可视化方式：
        - 右上角的点：显著上调基因
        - 左上角的点：显著下调基因
        - 中间的灰点：不显著基因

    参数:
        df: 带 'sig' 列的 DataFrame
        title: 图标题
        out_prefix: 输出文件前缀（不含扩展名）
    """
    fig, ax = plt.subplots(figsize=(10, 8))

    # 按分类分组画图（先画不显著的，再画显著的，确保显著的点在上面）
    for sig, color, label, alpha, size in [
        ('NS', colors['ns'], 'Not significant', 0.3, 8),
        ('Down', colors['down'], f'Down (padj<{PADJ_THRESH}, log2FC<-{LOG2FC_THRESH})', 0.7, 12),
        ('Up', colors['up'], f'Up (padj<{PADJ_THRESH}, log2FC>{LOG2FC_THRESH})', 0.7, 12),
    ]:
        subset = df[df['sig'] == sig]
        ax.scatter(subset['log2FoldChange'], subset['neg_log10_padj'],
                   c=color, alpha=alpha, s=size, edgecolors='none', label=label)

    # 画阈值线
    ax.axhline(y=-np.log10(PADJ_THRESH), color='gray', linestyle='--', linewidth=1, alpha=0.7)
    ax.axvline(x=LOG2FC_THRESH, color='gray', linestyle='--', linewidth=1, alpha=0.7)
    ax.axvline(x=-LOG2FC_THRESH, color='gray', linestyle='--', linewidth=1, alpha=0.7)

    # 标注 Top 10 上调和 Top 10 下调基因
    top_up = df[df['sig'] == 'Up'].nlargest(10, 'log2FoldChange')
    top_down = df[df['sig'] == 'Down'].nsmallest(10, 'log2FoldChange')

    for idx, row in pd.concat([top_up, top_down]).iterrows():
        # 使用 gene_id 作为标签。若你有 gene_name 映射，可替换为 gene_name
        ax.annotate(idx, xy=(row['log2FoldChange'], row['neg_log10_padj']),
                    xytext=(5, 5), textcoords='offset points',
                    fontsize=7, alpha=0.8,
                    bbox=dict(boxstyle='round,pad=0.2', fc='yellow', alpha=0.3))

    # 图例和标题
    ax.set_xlabel('log2 Fold Change', fontsize=13)
    ax.set_ylabel('-log10 (adjusted p-value)', fontsize=13)
    ax.set_title(title, fontsize=15, fontweight='bold')
    ax.legend(loc='upper right', fontsize=9, framealpha=0.9)
    ax.set_xlim(df['log2FoldChange'].min() - 1, df['log2FoldChange'].max() + 1)
    ax.set_ylim(0, df['neg_log10_padj'].max() + 1)

    # 加网格
    ax.grid(True, alpha=0.2, linestyle=':')

    # 保存
    fig.tight_layout()
    fig.savefig(f"{out_prefix}.png", dpi=300, bbox_inches='tight')
    fig.savefig(f"{out_prefix}.pdf", bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ 火山图已保存: {out_prefix}.png / .pdf")


# ============================================================================
# 函数 3：画 MA 图（Mean-Difference Plot）
# ============================================================================
def plot_ma(df, title, out_prefix):
    """
    MA 图：X 轴 = 平均表达量 (log10 baseMean)，Y 轴 = log2FoldChange。

    MA 图用来检查差异分析的质量：
        - 低表达基因的 fold change 应该有更多散点（方差大）。
        - 如果高表达基因也有很多极端 fold change，可能有问题。

    参数:
        df: 带 'sig' 列的 DataFrame
        title: 图标题
        out_prefix: 输出文件前缀
    """
    fig, ax = plt.subplots(figsize=(10, 8))

    # 添加一列 log10(baseMean)
    df['log10_baseMean'] = np.log10(df['baseMean'] + 1)

    for sig, color, label, alpha in [
        ('NS', colors['ns'], 'Not significant', 0.3),
        ('Down', colors['down'], 'Significant down', 0.7),
        ('Up', colors['up'], 'Significant up', 0.7),
    ]:
        subset = df[df['sig'] == sig]
        ax.scatter(subset['log10_baseMean'], subset['log2FoldChange'],
                   c=color, alpha=alpha, s=10, edgecolors='none', label=label)

    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    ax.axhline(y=LOG2FC_THRESH, color='gray', linestyle='--', linewidth=0.5)
    ax.axhline(y=-LOG2FC_THRESH, color='gray', linestyle='--', linewidth=0.5)

    ax.set_xlabel('log10 Mean Expression', fontsize=13)
    ax.set_ylabel('log2 Fold Change', fontsize=13)
    ax.set_title(title, fontsize=15, fontweight='bold')
    ax.legend(loc='upper right', fontsize=9)
    ax.set_ylim(df['log2FoldChange'].min() - 0.5, df['log2FoldChange'].max() + 0.5)
    ax.grid(True, alpha=0.2, linestyle=':')

    fig.tight_layout()
    fig.savefig(f"{out_prefix}.png", dpi=300, bbox_inches='tight')
    fig.savefig(f"{out_prefix}.pdf", bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ MA 图已保存: {out_prefix}.png / .pdf")


# ============================================================================
# 函数 4：输出 Top 差异基因表格
# ============================================================================
def export_top_genes_table(df, out_path, n_top=50):
    """
    把 Top 差异基因整理成漂亮的表格，保存为 CSV 和 Markdown。

    参数:
        df: 带 'sig' 列的 DataFrame
        out_path: 输出文件路径（不含扩展名）
        n_top: 提取前多少个基因
    """
    # 按 padj 排序，取最显著的
    top_genes = df[df['sig'] != 'NS'].sort_values('padj').head(n_top)

    # 保存为 CSV
    csv_path = f"{out_path}.csv"
    top_genes[['baseMean', 'log2FoldChange', 'lfcSE', 'stat', 'pvalue', 'padj', 'sig']].to_csv(csv_path)
    print(f"  ✅ Top 差异基因表已保存: {csv_path}")

    # 保存为 Markdown（方便直接贴到报告里）
    md_path = f"{out_path}.md"
    with open(md_path, 'w') as f:
        f.write(f"# Top {n_top} Differentially Expressed Genes\n\n")
        f.write(f"Threshold: padj < {PADJ_THRESH}, |log2FC| > {LOG2FC_THRESH}\n\n")
        f.write("| Rank | Gene ID | baseMean | log2FC | padj | Regulation |\n")
        f.write("|------|---------|----------|--------|------|------------|\n")
        for i, (gene_id, row) in enumerate(top_genes.iterrows(), 1):
            reg = "Up" if row['log2FoldChange'] > 0 else "Down"
            f.write(f"| {i} | {gene_id} | {row['baseMean']:.1f} | {row['log2FoldChange']:.2f} | {row['padj']:.2e} | {reg} |\n")
    print(f"  ✅ Markdown 表格已保存: {md_path}")

    return top_genes


# ============================================================================
# 函数 5：画多比较汇总图
# ============================================================================
def plot_comparison_summary(results_dict, out_prefix):
    """
    汇总多个比较的差异基因数量，画柱状图。

    参数:
        results_dict: dict，键是比较名，值是分类后的 DataFrame
        out_prefix: 输出前缀
    """
    summary_data = []
    for comp_name, df in results_dict.items():
        summary_data.append({
            'Comparison': comp_name,
            'Up': (df['sig'] == 'Up').sum(),
            'Down': (df['sig'] == 'Down').sum(),
        })

    summary_df = pd.DataFrame(summary_data)

    fig, ax = plt.subplots(figsize=(8, 6))
    x = np.arange(len(summary_df))
    width = 0.35

    bars1 = ax.bar(x - width/2, summary_df['Up'], width, label='Up', color=colors['up'], alpha=0.8)
    bars2 = ax.bar(x + width/2, summary_df['Down'], width, label='Down', color=colors['down'], alpha=0.8)

    ax.set_ylabel('Number of DEGs', fontsize=13)
    ax.set_title('Differentially Expressed Genes Summary', fontsize=15, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(summary_df['Comparison'], rotation=15, ha='right')
    ax.legend()
    ax.grid(True, alpha=0.2, axis='y')

    # 在柱子上标注数值
    for bars in [bars1, bars2]:
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f'{int(height)}',
                        xy=(bar.get_x() + bar.get_width()/2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=9)

    fig.tight_layout()
    fig.savefig(f"{out_prefix}.png", dpi=300, bbox_inches='tight')
    fig.savefig(f"{out_prefix}.pdf", bbox_inches='tight')
    plt.close(fig)
    print(f"  ✅ 汇总图已保存: {out_prefix}.png / .pdf")


# ============================================================================
# 主程序
# ============================================================================
def main():
    print("=" * 60)
    print("GSE60450 RNA-seq 可视化分析")
    print("=" * 60)

    # 要分析的比较列表：(文件名前缀, 标题)
    comparisons = [
        ("Luminal_vs_Basal_results", "Luminal vs Basal"),
        ("pregnant_vs_virgin_results", "Pregnant vs Virgin"),
        ("lactating_vs_virgin_results", "Lactating vs Virgin"),
    ]

    results_dict = {}

    for filename_prefix, title in comparisons:
        csv_path = os.path.join(RESULTS_DIR, f"{filename_prefix}.csv")

        if not os.path.exists(csv_path):
            print(f"\n⚠️  文件不存在，跳过: {csv_path}")
            continue

        print(f"\n📊 处理: {title}")
        print(f"   文件: {csv_path}")

        # 1. 读取并分类
        df = load_deseq2_results(csv_path)
        results_dict[title] = df

        # 2. 火山图
        out_prefix = os.path.join(FIG_DIR, f"volcano_{filename_prefix}")
        plot_volcano(df, f"Volcano Plot: {title}", out_prefix)

        # 3. MA 图
        out_prefix = os.path.join(FIG_DIR, f"MA_{filename_prefix}")
        plot_ma(df, f"MA Plot: {title}", out_prefix)

        # 4. Top 差异基因表
        out_prefix = os.path.join(RESULTS_DIR, f"top50_{filename_prefix}")
        export_top_genes_table(df, out_prefix, n_top=50)

    # 5. 多比较汇总图
    if results_dict:
        print("\n📊 生成多比较汇总图...")
        out_prefix = os.path.join(FIG_DIR, "DEG_summary")
        plot_comparison_summary(results_dict, out_prefix)

    print("\n" + "=" * 60)
    print("🎉 全部可视化完成！")
    print(f"图表保存在: {FIG_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
