#!/usr/bin/env Rscript
# ============================================================================
# deseq2_analysis.R
# GSE60450 小鼠乳腺 RNA-seq DESeq2 差异表达分析
# 输入：featureCounts 输出的 counts_matrix.txt
# 输出：差异表达结果表格、PCA 图、样本距离热图
#
# 运行方式：
#   Rscript deseq2_analysis.R
# 或：
#   打开 RStudio，逐段运行
# ============================================================================

# ---------------------------- 第 1 段：加载 R 包 ----------------------------
# DESeq2 是 RNA-seq 差异表达分析的行业标准包。
# 它基于负二项分布模型，考虑了 RNA-seq 数据的过离散（overdispersion）特性。
# tximport 用于导入 count 数据（这里直接从 featureCounts 读入）。
# ggplot2 和 pheatmap 用于可视化。
# BiocParallel 用于并行计算，加速 DESeq2。
suppressPackageStartupMessages({
  library(DESeq2)
  library(ggplot2)
  library(pheatmap)
  library(RColorBrewer)
  library(BiocParallel)
})

# ---------------------------- 第 2 段：设置参数和路径 ----------------------------
# PROJECT_DIR: 项目根目录（要和 run.sh 里的一致）
# RESULTS_DIR: 结果输出目录
# COUNTS_FILE: featureCounts 输出的计数矩阵
# 若你的 counts_matrix.txt 在其他位置，请修改下面路径。
PROJECT_DIR <- file.path(getwd(), "GSE60450_analysis")
RESULTS_DIR <- file.path(PROJECT_DIR, "results")
COUNTS_FILE <- file.path(PROJECT_DIR, "counts", "counts_matrix.txt")
SAMPLENAMES_FILE <- file.path("samplesheet.csv")  # 样本信息表

# 创建结果目录
dir.create(RESULTS_DIR, showWarnings = FALSE, recursive = TRUE)

# 设置并行：用 4 个核心跑 DESeq2（根据你的 CPU 调整）
register(MulticoreParam(4))

# ---------------------------- 第 3 段：读取计数矩阵 ----------------------------
# read.table: 读取制表符分隔的文本文件。
# header = TRUE: 第一行是列名（样本名）。
# row.names = 1: 第一列是行名（基因 ID）。
# check.names = FALSE: 不自动修改列名（保留原始 SRR 编号）。
counts <- read.table(COUNTS_FILE, header = TRUE, row.names = 1,
                     sep = "\t", check.names = FALSE, stringsAsFactors = FALSE)

# 查看矩阵维度：行数 = 基因数，列数 = 样本数
dim(counts)
# 查看前几行前几列
head(counts[, 1:3])

# ---------------------------- 第 4 段：构建样本信息表（coldata） ----------------------------
# DESeq2 需要一个 "coldata"（列数据）数据框，记录每个样本的元信息。
# 行名必须与 counts 矩阵的列名一致。
# 这里我们手动创建，因为 featureCounts 输出的列名是 BAM 文件路径。

# 从 counts 列名提取 SRR 编号
# 列名格式: path/to/SRRxxxx_Aligned.sortedByCoord.out.bam
# 我们用 basename() 提取文件名，再用 sub() 去掉后缀
colnames(counts)

# 提取样本名：从文件路径中提取 SRR 编号
sample_names <- basename(colnames(counts))
sample_names <- sub("_.*$", "", sample_names)  # 去掉 _Aligned... 后缀

# 构建 coldata
coldata <- data.frame(
  row.names = colnames(counts),
  sample = sample_names,
  stringsAsFactors = FALSE
)

# 根据 samplesheet.csv 或已知实验设计添加分组信息
# GSE60450: 2 细胞类型 × 3 状态 × 2 重复 = 12 样本
# SRR1552444-SRR1552449: Luminal (L)
# SRR1552450-SRR1552455: Basal (B)
# 其中每 2 个一组: virgin, pregnant, lactating
coldata$celltype <- ifelse(
  grepl("SRR15524[45]", coldata$sample), "Luminal",
  ifelse(grepl("SRR15524[67]", coldata$sample), "Luminal",
  ifelse(grepl("SRR15524[89]", coldata$sample), "Luminal", "Basal"))
)

# 更精确的方式：按 SRR 编号范围分组
# L virgin:   SRR1552444, SRR1552445
# L pregnant: SRR1552446, SRR1552447
# L lactating:SRR1552448, SRR1552449
# B virgin:   SRR1552450, SRR1552451
# B pregnant: SRR1552452, SRR1552453
# B lactating:SRR1552454, SRR1552455

coldata$celltype <- ifelse(
  coldata$sample %in% c("SRR1552444", "SRR1552445", "SRR1552446", "SRR1552447",
                         "SRR1552448", "SRR1552449"),
  "Luminal", "Basal"
)

coldata$status <- ifelse(
  coldata$sample %in% c("SRR1552444", "SRR1552445", "SRR1552450", "SRR1552451"),
  "virgin",
  ifelse(coldata$sample %in% c("SRR1552446", "SRR1552447", "SRR1552452", "SRR1552453"),
         "pregnant", "lactating")
)

# 合并为 group 列，方便后续分析
coldata$group <- paste(coldata$celltype, coldata$status, sep = "_")

# 转换为因子（factor），这是 DESeq2 的要求
coldata$celltype <- factor(coldata$celltype, levels = c("Basal", "Luminal"))
coldata$status <- factor(coldata$status, levels = c("virgin", "pregnant", "lactating"))
coldata$group <- factor(coldata$group)

# 检查 coldata 是否正确
print(coldata)

# ---------------------------- 第 5 段：创建 DESeqDataSet ----------------------------
# DESeqDataSetFromMatrix: 从计数矩阵创建 DESeq2 数据对象。
# countData: 计数矩阵（基因 × 样本）。
# colData: 样本元信息。
# design: 实验设计公式，告诉 DESeq2 哪些因子影响基因表达。
#   ~ celltype + status 表示同时建模细胞类型和发育状态的主效应。
#   这意味着 DESeq2 会在控制另一个因子的同时，检测每个因子的效应。
dds <- DESeqDataSetFromMatrix(
  countData = counts,
  colData = coldata,
  design = ~ celltype + status
)

# 查看对象
print(dds)

# ---------------------------- 第 6 段：预过滤低表达基因 ----------------------------
# RNA-seq 中有很多基因在所有样本中表达量都极低（0 或 1）。
# 这些基因不仅无生物学意义，还会增加多重检验校正的负担。
# 过滤标准：在所有样本中总 count < 10 的基因去掉。
# 这个阈值是经验性的，通常 5-10 都可以。
keep <- rowSums(counts(dds)) >= 10
dds <- dds[keep, ]

message(paste0("过滤后剩余基因数: ", nrow(dds)))

# ---------------------------- 第 7 段：运行 DESeq2 差异分析 ----------------------------
# DESeq(): 执行完整的差异分析流程，内部包括：
#   1. size factor 估计（消除测序深度差异）
#   2. 离散度（dispersion）估计（负二项分布的方差参数）
#   3. 广义线性模型拟合（negative binomial GLM）
#   4. Wald 检验（或 LRT）计算 p 值
# parallel = TRUE: 使用之前注册的 BiocParallel 并行计算。
dds <- DESeq(dds, parallel = TRUE)

# 查看 size factors（测序深度标准化因子）
# 理想情况下应该在 0.5 ~ 2 之间，如果某个样本偏离太多，说明该样本有问题。
message("Size factors (测序深度标准化因子):")
print(sizeFactors(dds))

# ---------------------------- 第 8 段：提取差异分析结果 ----------------------------
# results(): 提取特定比较的差异结果。
# contrast: 指定要比较的分组。
#   格式: c("因子名", "实验组", "对照组")
#   这里 "Luminal" vs "Basal"，Basal 是对照组。
# alpha = 0.05: 显著性水平，用于独立过滤（independent filtering）。

# ---- 比较 1: Luminal vs Basal (主要比较，跨所有发育状态) ----
res_celltype <- results(dds,
                        contrast = c("celltype", "Luminal", "Basal"),
                        alpha = 0.05)

# 查看结果摘要
message("\n=== Luminal vs Basal 差异分析摘要 ===")
summary(res_celltype)

# 结果表格说明：
#   baseMean: 该基因在所有样本中的平均标准化计数
#   log2FoldChange: 实验组 vs 对照组的 log2 倍数变化
#                   正值 = 在 Luminal 中高表达；负值 = 在 Basal 中高表达
#   lfcSE: log2 fold change 的标准误
#   stat: Wald 统计量
#   pvalue: 原始 p 值
#   padj: BH 校正后的 p 值（即 FDR，多重检验校正后的显著性）

# ---- 比较 2: pregnant vs virgin ----
res_preg <- results(dds,
                    contrast = c("status", "pregnant", "virgin"),
                    alpha = 0.05)
message("\n=== pregnant vs virgin 差异分析摘要 ===")
summary(res_preg)

# ---- 比较 3: lactating vs virgin ----
res_lact <- results(dds,
                    contrast = c("status", "lactating", "virgin"),
                    alpha = 0.05)
message("\n=== lactating vs virgin 差异分析摘要 ===")
summary(res_lact)

# ---------------------------- 第 9 段：添加基因符号 ----------------------------
# featureCounts 输出的是 GENCODE 的 gene_id（如 ENSMUSG00000000001）。
# 为了可读性，我们把 gene_name（如 Gnai3）加到结果里。
# 这里简单演示：假设我们有一个 gene_id -> gene_name 的映射。
# 实际分析中，可用 biomaRt 或 AnnotationDbi 包从 Ensembl ID 转换。

# 保存原始结果（供 Python 可视化脚本读取）
write.csv(as.data.frame(res_celltype),
          file = file.path(RESULTS_DIR, "Luminal_vs_Basal_results.csv"))
write.csv(as.data.frame(res_preg),
          file = file.path(RESULTS_DIR, "pregnant_vs_virgin_results.csv"))
write.csv(as.data.frame(res_lact),
          file = file.path(RESULTS_DIR, "lactating_vs_virgin_results.csv"))

message(paste0("\n差异分析结果已保存到 ", RESULTS_DIR))

# ---------------------------- 第 10 段：提取 Top 差异基因 ----------------------------
# 筛选标准：padj < 0.05 且 |log2FC| > 1（即倍数变化 > 2 或 < 0.5）
# 这是 RNA-seq 中最常用的筛选标准。

# Luminal vs Basal
res_celltype_sig <- subset(res_celltype,
                           padj < 0.05 & abs(log2FoldChange) > 1)
# 按 padj 排序（最显著的在前）
res_celltype_sig <- res_celltype_sig[order(res_celltype_sig$padj), ]

message(paste0("\nLuminal vs Basal 显著差异基因数 (padj<0.05 & |log2FC|>1): ",
               nrow(res_celltype_sig)))

# 保存 Top 50 差异基因表
write.csv(as.data.frame(head(res_celltype_sig, 50)),
          file = file.path(RESULTS_DIR, "top50_Luminal_vs_Basal.csv"))

# 同样处理其他比较
res_preg_sig <- subset(res_preg, padj < 0.05 & abs(log2FoldChange) > 1)
res_preg_sig <- res_preg_sig[order(res_preg_sig$padj), ]
write.csv(as.data.frame(head(res_preg_sig, 50)),
          file = file.path(RESULTS_DIR, "top50_pregnant_vs_virgin.csv"))

res_lact_sig <- subset(res_lact, padj < 0.05 & abs(log2FoldChange) > 1)
res_lact_sig <- res_lact_sig[order(res_lact_sig$padj), ]
write.csv(as.data.frame(head(res_lact_sig, 50)),
          file = file.path(RESULTS_DIR, "top50_lactating_vs_virgin.csv"))

# ---------------------------- 第 11 段：PCA 分析 ----------------------------
# vst(): Variance Stabilizing Transformation，对 count 数据进行变换，
# 使方差与均值无关（类似 log 变换，但更适合低表达基因）。
# 变换后的数据用于 PCA 和聚类可视化。
vst_data <- vst(dds, blind = FALSE)

# plotPCA(): DESeq2 内置的 PCA 画图函数。
# intgroup: 按哪些分组变量着色。
# ntop: 用多少个高变基因做 PCA（默认 500）。
# 返回 ggplot 对象，可继续用 ggplot2 语法美化。
pca_plot <- plotPCA(vst_data, intgroup = c("celltype", "status"), ntop = 500) +
  ggtitle("PCA: GSE60450 小鼠乳腺 RNA-seq") +
  theme_bw(base_size = 14) +
  scale_color_brewer(palette = "Set1")

print(pca_plot)
ggsave(file.path(RESULTS_DIR, "PCA_plot.pdf"), pca_plot, width = 8, height = 6)
ggsave(file.path(RESULTS_DIR, "PCA_plot.png"), pca_plot, width = 8, height = 6, dpi = 300)

# ---------------------------- 第 12 段：样本距离热图 ----------------------------
# dist(): 计算样本间欧氏距离矩阵。
# 距离越近 = 表达模式越相似。
# 理想情况下，生物学重复应该聚类在一起。

# 提取 vst 变换后的计数矩阵
vst_mat <- assay(vst_data)

# 取高变基因（前 1000 个）用于距离计算，避免噪音基因干扰
var_genes <- order(rowVars(vst_mat), decreasing = TRUE)[1:1000]
vst_mat_top <- vst_mat[var_genes, ]

# 计算样本间距离
sample_dists <- dist(t(vst_mat_top))
sample_dist_matrix <- as.matrix(sample_dists)

# 设置颜色： brewer.pal(9, "Blues") 是蓝白渐变色板
# colorRampPalette 扩展为 100 级渐变
colors <- colorRampPalette(rev(brewer.pal(9, "Blues")))(100)

# 注释条：在热图上方显示 celltype 和 status
annotation <- data.frame(
  CellType = coldata$celltype,
  Status = coldata$status,
  row.names = rownames(coldata)
)
ann_colors <- list(
  CellType = c(Luminal = "#E41A1C", Basal = "#377EB8"),
  Status = c(virgin = "#4DAF4A", pregnant = "#FF7F00", lactating = "#984EA3")
)

# 画热图
pdf(file.path(RESULTS_DIR, "sample_distance_heatmap.pdf"), width = 10, height = 8)
pheatmap(sample_dist_matrix,
         clustering_distance_rows = sample_dists,
         clustering_distance_cols = sample_dists,
         col = colors,
         annotation_col = annotation,
         annotation_row = annotation,
         annotation_colors = ann_colors,
         main = "Sample Distance Heatmap (Top 1000 Variable Genes)")
dev.off()

# ---------------------------- 第 13 段：Top 差异基因表达热图 ----------------------------
# 选择 Luminal vs Basal 最显著的 Top 20 基因，画表达热图。
# 热图行 = 基因，列 = 样本，颜色 = 表达量。

top_genes <- rownames(head(res_celltype_sig, 20))
heatmap_mat <- assay(vst_data)[top_genes, ]

# 对每行（基因）做 Z-score 标准化，使颜色深浅反映相对表达水平
heatmap_mat <- t(scale(t(heatmap_mat)))

pdf(file.path(RESULTS_DIR, "top20_DEG_heatmap.pdf"), width = 10, height = 8)
pheatmap(heatmap_mat,
         cluster_rows = TRUE,
         cluster_cols = TRUE,
         annotation_col = annotation,
         annotation_colors = ann_colors,
         color = colorRampPalette(rev(brewer.pal(9, "RdBu")))(100),
         main = "Top 20 DEGs: Luminal vs Basal",
         fontsize_row = 8)
dev.off()

message("\n========================================")
message("🎉 DESeq2 分析完成！")
message(paste0("结果目录: ", RESULTS_DIR))
message("输出文件列表:")
message("  - Luminal_vs_Basal_results.csv      (完整差异分析结果)")
message("  - top50_Luminal_vs_Basal.csv        (Top 50 差异基因)")
message("  - PCA_plot.pdf / .png               (PCA 图)")
message("  - sample_distance_heatmap.pdf       (样本距离热图)")
message("  - top20_DEG_heatmap.pdf             (Top 20 差异基因热图)")
message("========================================")
