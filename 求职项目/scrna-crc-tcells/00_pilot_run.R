# ============================================================================
# 00_pilot_run.R — 毕设迷你彩排脚本（减量版，预计 1–2 小时跑完）
#
# 目的：用真实毕设数据（GSE108989）的小样本跑通转录组主线流程，
#       熟悉操作、积累汇报素材。这不是正式分析，正式分析跑 01/02 脚本。
#
# 环境：实验室服务器（Linux），R >= 4.2，已装 Seurat/dplyr/ggplot2/patchwork/data.table
# 运行：Rscript 00_pilot_run.R     （或 RStudio 里逐段运行）
#
# 减量策略：只保留 2 个患者（P0123, P0309）的细胞，约 2000 个以内，
#           全流程几分钟到几十分钟跑完；流程与正式分析完全一致。
# ============================================================================

library(data.table)   # 快速读大表
library(dplyr)
library(Seurat)
library(patchwork)
library(ggplot2)

set.seed(42)
dir.create("pilot_results", showWarnings = FALSE)

# ---------- 第 0 步：下载数据（69.7MB，几分钟） ----------
count_url <- "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE108nnn/GSE108989/suppl/GSE108989_CRC.TCell.S11138.count.txt.gz"
count_gz  <- "GSE108989_counts.txt.gz"
if (!file.exists(count_gz)) {
  message("下载 count 矩阵（69.7MB）...")
  download.file(count_url, count_gz, mode = "wb")
}

# ---------- 第 1 步：读入矩阵并减量 ----------
# 矩阵格式：行=基因，列=细胞（11138 个细胞），第一列是基因名
message("读取矩阵（约1-2分钟）...")
counts <- fread(count_gz)
gene_col <- colnames(counts)[1]
genes <- counts[[gene_col]]
counts <- as.matrix(counts[, -1, with = FALSE])
rownames(counts) <- genes

# 从细胞名解析患者ID（细胞名形如 NTH5-20180123，患者编号在GEO网页注释表；
# 简化做法：按列名后缀日期前缀猜患者不稳妥，直接随机抽样 2000 细胞做彩排）
keep_cells <- sample(colnames(counts), min(2000, ncol(counts)))
counts_small <- counts[, keep_cells]
message("彩排规模：", ncol(counts_small), " 个细胞 × ", nrow(counts_small), " 个基因")

# ---------- 第 2 步：构建 Seurat 对象 + 质控 ----------
# Smart-seq2 是全长测序，每个细胞测到的基因数远多于 10x，
# 所以质控阈值与 10x 教程不同：nFeature 下限设 1000 而非 200
seu <- CreateSeuratObject(counts = counts_small, project = "CRC_Tcell_pilot",
                          min.cells = 3, min.features = 500)
seu[["percent.mt"]] <- PercentageFeatureSet(seu, pattern = "^MT-")

p_qc <- VlnPlot(seu, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"),
                ncol = 3, pt.size = 0.1)
ggsave("pilot_results/01_QC_violin.png", p_qc, width = 10, height = 4)

seu <- subset(seu, subset = nFeature_RNA > 1000 & nFeature_RNA < 12000 & percent.mt < 10)
message("质控后剩余细胞数：", ncol(seu))

# ---------- 第 3 步：标准化 → 降维 → 聚类 ----------
seu <- NormalizeData(seu)
seu <- FindVariableFeatures(seu, nfeatures = 2000)
seu <- ScaleData(seu)
seu <- RunPCA(seu, npcs = 30, verbose = FALSE)
seu <- FindNeighbors(seu, dims = 1:20)
seu <- FindClusters(seu, resolution = 0.8)
seu <- RunUMAP(seu, dims = 1:20)

p_umap <- DimPlot(seu, reduction = "umap", label = TRUE) + ggtitle("Pilot: 2000 cells")
ggsave("pilot_results/02_UMAP_clusters.png", p_umap, width = 7, height = 6)

# ---------- 第 4 步：marker 基因 + 快速亚型注释 ----------
markers <- FindAllMarkers(seu, only.pos = TRUE, min.pct = 0.25,
                          logfc.threshold = 0.25, verbose = FALSE)
write.csv(markers, "pilot_results/03_markers.csv", row.names = FALSE)

# 用经典 T 细胞 marker 给每个cluster投票注释（正式分析用更细致流程，此处求快求懂）
marker_map <- list(
  "CD8_T"  = c("CD8A", "CD8B", "GZMK"),
  "CD4_T"  = c("CD4", "IL7R", "CCR7"),
  "Treg"   = c("FOXP3", "IL2RA", "CTLA4"),
  "耗竭T"  = c("PDCD1", "HAVCR2", "LAG3", "TOX"),
  "增殖T"  = c("MKI67", "TOP2A"),
  "NK样"   = c("NKG7", "GNLY", "KLRD1")
)
avg <- AverageExpression(seu, features = unique(unlist(marker_map)))$RNA
clu <- colnames(avg)
score_tab <- sapply(marker_map, function(gs) colMeans(avg[intersect(gs, rownames(avg)), , drop = FALSE]))
best <- colnames(score_tab)[max.col(score_tab, ties.method = "first")]
names(best) <- clu
seu$cell_type <- best[as.character(Idents(seu))]

p_anno <- DimPlot(seu, reduction = "umap", group.by = "cell_type", label = TRUE) +
          ggtitle("Pilot: 亚型注释（快速投票法）")
ggsave("pilot_results/04_UMAP_annotated.png", p_anno, width = 8, height = 6)

# ---------- 第 5 步：功能评分（毕设核心指标） ----------
seu <- AddModuleScore(seu, features = list(c("GZMA","GZMB","PRF1","NKG7","GNLY")),
                      name = "Cytotoxic_score")
seu <- AddModuleScore(seu, features = list(c("PDCD1","HAVCR2","LAG3","TIGIT","TOX","CTLA4")),
                      name = "Exhaustion_score")

p_score <- VlnPlot(seu, features = c("Cytotoxic_score1", "Exhaustion_score1"),
                   group.by = "cell_type", pt.size = 0, ncol = 2)
ggsave("pilot_results/05_scores.png", p_score, width = 12, height = 5)

p_feat <- FeaturePlot(seu, features = c("Exhaustion_score1"), reduction = "umap")
ggsave("pilot_results/06_exhaustion_umap.png", p_feat, width = 7, height = 6)

saveRDS(seu, "pilot_results/pilot_seurat.rds")

message("==================== 彩排完成 ====================")
message("产出 6 张图 + marker表，全部在 pilot_results/ 目录")
message("汇报口径：用真实毕设数据(GSE108989, Smart-seq2)的2000细胞子集跑通了")
message("『质控→聚类→注释→功能评分』主线，全流程XX分钟。")
