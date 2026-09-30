# =============================================================================
# 01_qc_clustering.R
# 项目：GSE108989 结直肠癌浸润 T 细胞单细胞转录组分析（转录组前段）
# 本脚本任务：数据获取 -> Seurat 质控 -> 标准化 -> 降维聚类 -> UMAP ->
#             找 marker 基因 -> T 细胞亚型注释 -> 输出图
# 作者：毕设项目  日期：2025
# =============================================================================
#
# 【数据集说明】GSE108989（Zhang et al., Nature 2018）
#   - 平台：Smart-seq2（全长转录本，读数深、每个细胞基因数多）
#   - 内容：12 例结直肠癌患者，~11,000 个 T 细胞（肿瘤/癌旁/外周血）
#   - 表达量单位：TPM（Smart-seq2 论文通常提供 TPM，而不是 UMI count）
#
# 【数据获取方式一：用 GEOquery 自动下载（推荐，可复现）】
#   运行下面这段即可（默认注释掉，防止每次运行重复下载几百 MB）：
#
#   library(GEOquery)
#   # GSE108989 的补充文件中含 TPM 表达矩阵（约几百 MB，注意硬盘空间）
#   getGEOSuppFiles("GSE108989", baseDir = "data")
#   # 下载完成后解压（文件名以实际下载为准，常见为 GSE108989_CRC.TCell....txt.gz）
#   untar("data/GSE108989/GSE108989_RAW.tar", exdir = "data/GSE108989")  # 若为 tar 包
#
# 【数据获取方式二：手动下载（网络不好时用）】
#   1. 浏览器打开 https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE108989
#   2. 拉到页面底部 "Supplementary file"，下载 TPM 表达矩阵 txt.gz
#   3. 放到本项目的 data/ 目录下，并把下面 expr_path 改成实际文件名
#
# 【体积与内存预估】
#   - 原始矩阵 txt.gz：约 200~500 MB，解压后文本约 1~2 GB
#   - 读入 R 后稀疏矩阵：约 1~2 GB 内存；Seurat 对象约 2~4 GB
#   - 建议电脑内存 >= 8 GB，留 >= 5 GB 空闲硬盘
# =============================================================================

## ---- 0. 加载 R 包 ----------------------------------------------------------
# Seurat：单细胞分析主框架；dplyr：数据处理语法；patchwork：拼图；ggplot2：画图
suppressPackageStartupMessages({
  library(Seurat)
  library(dplyr)
  library(patchwork)
  library(ggplot2)
})

set.seed(42)  # 固定随机种子，保证 UMAP/聚类结果可复现

dir.create("data",    showWarnings = FALSE)  # 存放原始数据
dir.create("results", showWarnings = FALSE)  # 存放图表与 rds

## ---- 1. 读入表达矩阵 -------------------------------------------------------
# expr_path 指向解压后的 TPM 矩阵：行=基因，列=细胞，第一列为基因名。
# 【请把文件名改成你实际下载解压后的文件名】
expr_path <- "data/GSE108989_TPM.txt"

if (!file.exists(expr_path)) {
  stop(
    "找不到表达矩阵：", expr_path, "\n",
    "请按脚本开头注释的两种方式之一下载 GSE108989 TPM 矩阵，",
    "放到 data/ 目录并把 expr_path 改为实际文件名。"
  )
}

# data.table::fread 比 read.table 快 10 倍以上，读这种大矩阵必须用它
expr_df <- data.table::fread(expr_path, data.table = FALSE)
rownames(expr_df) <- expr_df[[1]]   # 第一列是基因名，设为行名
expr_df <- expr_df[, -1]            # 删掉基因名列，剩下的全数值
expr_mat <- as.matrix(expr_df)

# Smart-seq2 的 TPM 是小数且动态范围大，Seurat 流程要求输入"近似 count"的非负数据。
# 本分析沿用论文社区常见做法：直接以 TPM 作为输入，后续用 LogNormalize。
# 注意：若想与原文完全一致，原文用的是 RSEM/TPM + log2(TPM/10+1) 的自建流程，
# 我们这里用 Seurat 标准流程做毕设级别的复现，结论趋势一致。

## ---- 2. 创建 Seurat 对象 ----------------------------------------------------
# min.cells = 3：一个基因至少在 3 个细胞中表达才保留，去掉测序噪音带来的"假基因"
# min.features = 200：一个细胞至少检测到 200 个基因才保留，去掉空孔/破碎细胞
seu <- CreateSeuratObject(counts = expr_mat, project = "CRC_T",
                          min.cells = 3, min.features = 200)
cat("初始细胞数：", ncol(seu), "，基因数：", nrow(seu), "\n")

## ---- 3. 质控（QC）----------------------------------------------------------
# 计算每个细胞线粒体基因比例。Smart-seq2 是全长测序，线粒体基因以 "MT-" 开头。
# 线粒体比例高通常说明细胞膜破损、胞质 mRNA 流失 -> 低质量或濒死细胞。
seu[["percent.mt"]] <- PercentageFeatureSet(seu, pattern = "^MT-")

# 【Smart-seq2 与 10x 的 QC 阈值差异 —— 毕设答辩高频问题】
# 10x 是 3' 端 UMI 计数，一个细胞通常只测到 1000~4000 个基因，
#   常用阈值：200 < nFeature < 5000（或 6000），percent.mt < 5~10%。
# Smart-seq2 是全长转录本、测序更深，一个细胞轻松测到 5000~10000 个基因，
#   因此 nFeature 上限要放宽，否则会把真实高质量细胞误删；
#   下限提高到 1000 左右，过滤明显低质量的细胞。
# 线粒体阈值用 10%：T 细胞代谢活跃，且 GSE108989 是消化后的组织细胞，
#   比 10x PBMC 略宽松，避免误杀活细胞。
qc_min_features <- 1000   # Smart-seq2 细胞至少 1000 个基因
qc_max_features <- 12000  # 上限防双细胞（doublet）——两个细胞进了一个孔
qc_max_mt       <- 10     # 线粒体比例上限 10%

# 画 QC 小提琴图（过滤前），检查分布是否合理
p_qc_before <- VlnPlot(seu, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"),
                       ncol = 3, pt.size = 0)
ggsave("results/QC_violin_before_filter.pdf", p_qc_before, width = 10, height = 4)

seu <- subset(seu, subset = nFeature_RNA > qc_min_features &
                         nFeature_RNA < qc_max_features &
                         percent.mt  < qc_max_mt)
cat("QC 后细胞数：", ncol(seu), "\n")

p_qc_after <- VlnPlot(seu, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"),
                      ncol = 3, pt.size = 0)
ggsave("results/QC_violin_after_filter.pdf", p_qc_after, width = 10, height = 4)

## ---- 4. 标准化 + 高变基因 ---------------------------------------------------
# LogNormalize：每个细胞的总表达量缩放到 10000，再 log1p。
# 目的：消除细胞间测序深度差异。Smart-seq2 无 UMI，深度差异比 10x 更大，必须做。
seu <- NormalizeData(seu, normalization.method = "LogNormalize", scale.factor = 10000)

# 找 2000 个高变基因（HVG）：只用这些"信息量大"的基因做降维，
# 能降噪并大幅加速。2000 是 Seurat 默认且广泛验证的值。
seu <- FindVariableFeatures(seu, selection.method = "vst", nfeatures = 2000)

## ---- 5. 缩放 + PCA -----------------------------------------------------------
# ScaleData：把每个基因缩放到均值 0、方差 1，使高表达基因不主导 PCA。
# vars.to.regress = "percent.mt"：回归掉线粒体比例的残留影响（可选，常用）。
seu <- ScaleData(seu, features = VariableFeatures(seu),
                 vars.to.regress = "percent.mt")

seu <- RunPCA(seu, npcs = 50, verbose = FALSE)

# 用 ElbowPlot 看主成分"拐点"：拐点之后的 PC 基本是噪音
pdf("results/PCA_elbow.pdf", width = 5, height = 4)
print(ElbowPlot(seu, ndims = 50))
dev.off()

## ---- 6. 聚类 + UMAP -----------------------------------------------------------
# 选前 20 个 PC：T 细胞数据集经验值，拐点通常在 10~20 之间，取 20 留余量。
n_pc <- 20

# FindNeighbors 构建 KNN 图；FindClusters 用 Louvain/Leiden 社区发现聚类。
# resolution = 0.5：分辨率，越大分得越细。T 细胞亚型 0.5~0.8 常用，先用 0.5。
seu <- FindNeighbors(seu, dims = 1:n_pc)
seu <- FindClusters(seu, resolution = 0.5)

# UMAP 非线性降维用于可视化（注意：聚类是在 PC 空间做的，不是在 UMAP 上做的）
seu <- RunUMAP(seu, dims = 1:n_pc)

p_umap_cluster <- DimPlot(seu, reduction = "umap", label = TRUE, pt.size = 0.4) +
  ggtitle("Seurat clusters (resolution = 0.5)")
ggsave("results/UMAP_clusters.pdf", p_umap_cluster, width = 7, height = 6)

## ---- 7. 找每个 cluster 的 marker 基因 -----------------------------------------
# FindAllMarkers：每个 cluster vs 其余所有细胞，Wilcoxon 秩和检验。
# only.pos = TRUE：只保留在该 cluster 中高表达的基因（正向 marker 更利于注释）
# min.pct = 0.25：基因至少在 25% 的细胞中表达，避免稀有噪音基因
# logfc.threshold = 0.25：常用默认值，兼顾灵敏度与特异性
markers <- FindAllMarkers(seu, only.pos = TRUE, min.pct = 0.25,
                          logfc.threshold = 0.25, test.use = "wilcox")
write.csv(markers, "results/cluster_markers.csv", row.names = FALSE)

# 每个 cluster 取 top5 marker 画热图
top5 <- markers %>% group_by(cluster) %>% slice_max(order_by = avg_log2FC, n = 5)
p_heat <- DoHeatmap(seu, features = unique(top5$gene), size = 3) + NoLegend()
ggsave("results/marker_heatmap.pdf", p_heat, width = 10, height = 8)

## ---- 8. T 细胞亚型注释 ----------------------------------------------------------
# 用经典 marker 给 cluster 贴生物学标签：
#   CD8 T  : CD8A、CD8B 高（CD8 是异二聚体，两个亚基都要看，单看 CD8A 会混入 NK）
#   CD4 T  : CD4 高，且 CD8A/CD8B 低
#   Treg   : FOXP3 + IL2RA(CD25) 高，是免疫抑制型调节 T 细胞
#   增殖型 : MKI67、TOP2A 高（细胞周期 G2/M 标志），不分 CD4/CD8 单独成类
#   耗竭倾向: PDCD1、HAVCR2 高（正式耗竭评分在 02 脚本做，这里只辅助判断）
# 下面先画 marker 的 FeaturePlot 人工核对，再用"打分法"自动注释。

classic_markers <- c("CD4", "CD8A", "CD8B", "FOXP3", "IL2RA",
                     "MKI67", "TOP2A", "PDCD1", "HAVCR2", "NKG7")
p_feat <- FeaturePlot(seu, features = classic_markers, ncol = 5, pt.size = 0.3)
ggsave("results/UMAP_classic_markers.pdf", p_feat, width = 18, height = 8)

# 自动注释：对每个 cluster 的 marker 列表做规则匹配
# （实际使用时请务必对照上面的 FeaturePlot 和 cluster_markers.csv 人工确认！）
annotate_cluster <- function(cl, marker_tbl) {
  genes <- marker_tbl %>% filter(cluster == cl) %>%
    slice_max(order_by = avg_log2FC, n = 50) %>% pull(gene)
  has <- function(g) any(g %in% genes)
  if (has(c("MKI67", "TOP2A")))            return("Proliferating")
  if (has(c("FOXP3", "IL2RA")))            return("Treg")
  if (all(c("CD8A", "CD8B") %in% genes))   return("CD8_T")
  if ("CD4" %in% genes)                    return("CD4_T")
  return("Other_T")
}

cluster_ids <- levels(Idents(seu))
ann <- sapply(cluster_ids, annotate_cluster, marker_tbl = markers)
cat("自动注释结果：\n"); print(ann)

# 把注释写回 Seurat 对象，成为新的一列 meta.data
seu$cell_type <- plyr::mapvalues(as.character(Idents(seu)),
                                 from = cluster_ids, to = ann)
Idents(seu) <- seu$cell_type

p_umap_annotated <- DimPlot(seu, reduction = "umap", label = TRUE, pt.size = 0.4) +
  ggtitle("Annotated T cell subtypes")
ggsave("results/UMAP_annotated.pdf", p_umap_annotated, width = 7, height = 6)

## ---- 9. 保存对象，供下游脚本使用 ------------------------------------------------
saveRDS(seu, "results/seu_annotated.rds")
cat("脚本 01 完成。输出文件均在 results/ 目录：\n")
cat("  QC_violin_before/after_filter.pdf, PCA_elbow.pdf,\n")
cat("  UMAP_clusters.pdf, UMAP_classic_markers.pdf, UMAP_annotated.pdf,\n")
cat("  marker_heatmap.pdf, cluster_markers.csv, seu_annotated.rds\n")
