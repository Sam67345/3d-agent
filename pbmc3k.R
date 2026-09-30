# ============================================================================
# pbmc3k.R
# Seurat 官方 PBMC 3k 教程完整脚本（Seurat v5 版本）
# 适用人群：生物技术本科生，R 零基础
# 运行方式：打开 RStudio → File → Open File → 选中此文件 → 全选代码 → 点击 Run
# ============================================================================

# ---------------- 第 1 段：加载必要的 R 包 ----------------
# dplyr 用于数据处理（如 %>% 管道操作），
# Seurat 是单细胞分析的核心包，
# patchwork 用于把多个 ggplot2 图拼在一起。
# 如果报错说找不到包，请先运行安装命令（见配套文档）。
library(dplyr)
library(Seurat)
library(patchwork)

# ---------------- 第 2 段：自动下载 PBMC3k 数据 ----------------
# 这段代码会自动从 10x Genomics 官网下载数据并解压到当前文件夹。
# 数据大小约 30 MB，下载时间取决于网速。
# 如果已经下载过，再次运行时会跳过重复下载。
data_url <- "https://cf.10xgenomics.com/samples/cell/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz"
data_tar <- "pbmc3k_filtered_gene_bc_matrices.tar.gz"
data_dir <- "filtered_gene_bc_matrices/hg19"

if (!dir.exists(data_dir)) {
  message("正在下载 PBMC3k 数据，请稍候...")
  download.file(url = data_url, destfile = data_tar, mode = "wb")
  message("正在解压数据...")
  untar(tarfile = data_tar)
  message("数据准备完毕！")
} else {
  message("检测到数据已存在，跳过下载。")
}

# ---------------- 第 3 段：读取数据并创建 Seurat 对象 ----------------
# Read10X() 读取 10x Genomics 标准输出格式的 count matrix。
# 返回的矩阵行是基因，列是细胞，数值是每个细胞中检测到的分子数（UMI）。
# CreateSeuratObject() 用 count matrix 创建 Seurat 对象——
# 它是单细胞数据的"容器"，后续所有分析结果都会存在里面。
# min.cells = 3：只在 ≥3 个细胞中表达的基因才保留（去掉极稀有基因）。
# min.features = 200：只保留测到 ≥200 个基因的细胞（去掉空液滴/死细胞）。
pbmc.data <- Read10X(data.dir = data_dir)
pbmc <- CreateSeuratObject(counts = pbmc.data, project = "pbmc3k", min.cells = 3, min.features = 200)

# 查看 Seurat 对象的基本信息：有多少个基因、多少个细胞
pbmc

# ---------------- 第 4 段：查看原始 count matrix 的样子 ----------------
# 稀疏矩阵中 "." 代表 0（该基因在这个细胞里没被检测到）。
# 单细胞数据非常稀疏（90% 以上都是 0），所以 Seurat 用稀疏矩阵存储，省内存。
pbmc.data[c("CD3D", "TCL1A", "MS4A1"), 1:30]

# ---------------- 第 5 段：计算质控（QC）指标 ----------------
# 单细胞分析最关键的第一步是 QC——去掉低质量细胞。
# 三个核心 QC 指标：
#   1. nFeature_RNA：每个细胞检测到的基因数（太少 = 死细胞/空液滴；太多 = 双细胞）。
#   2. nCount_RNA：每个细胞检测到的总分子数（与基因数高度相关）。
#   3. percent.mt：线粒体基因占比（太高 = 细胞膜破裂，细胞质 RNA 流失，只剩线粒体）。
# PercentageFeatureSet() 会自动找出所有以 "MT-" 开头的线粒体基因并计算比例。
pbmc[["percent.mt"]] <- PercentageFeatureSet(pbmc, pattern = "^MT-")

# 查看前 5 个细胞的 QC 指标
head(pbmc@meta.data, 5)

# ---------------- 第 6 段：QC 指标可视化 ----------------
# VlnPlot() 画小提琴图，展示三个 QC 指标在整个数据集中的分布。
# FeatureScatter() 画散点图，看两个指标之间的关系。
# 这些图帮助你判断该用什么样的阈值过滤细胞。
VlnPlot(pbmc, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"), ncol = 3)

plot1 <- FeatureScatter(pbmc, feature1 = "nCount_RNA", feature2 = "percent.mt")
plot2 <- FeatureScatter(pbmc, feature1 = "nCount_RNA", feature2 = "nFeature_RNA")
plot1 + plot2

# ---------------- 第 7 段：根据 QC 指标过滤低质量细胞 ----------------
# 这里采用的过滤标准（来自 Seurat 官方教程）：
#   - 基因数：200 ~ 2500（去掉基因太少的死细胞和基因太多的双细胞）。
#   - 线粒体比例：< 5%（去掉濒死细胞）。
# 注意：不同组织、不同实验的 QC 阈值可能不同，要根据上面的小提琴图灵活调整。
pbmc <- subset(pbmc, subset = nFeature_RNA > 200 & nFeature_RNA < 2500 & percent.mt < 5)

# ---------------- 第 8 段：数据标准化（Normalization） ----------------
# 每个细胞测到的总分子数不同（测序深度不同），不能直接比较。
# LogNormalize 的做法：把每个细胞的基因计数除以其总计数，再乘 10,000（得到 CPM 类似的值），
# 最后取 log1p（log(1 + x)）压缩数据的动态范围。
# 标准化后的数据存在 pbmc[["RNA"]]$data 中。
pbmc <- NormalizeData(pbmc, normalization.method = "LogNormalize", scale.factor = 10000)

# ---------------- 第 9 段：识别高变基因（Feature Selection） ----------------
# 单细胞数据集通常有 1~2 万个基因，但大部分基因在所有细胞中表达量都差不多，
# 对区分细胞类型没有帮助。
# FindVariableFeatures() 找出"在某些细胞中高表达、在另一些细胞中低表达"的基因，
# 默认返回 2000 个高变基因。后续 PCA、聚类都只基于这些基因进行，减少噪音。
pbmc <- FindVariableFeatures(pbmc, selection.method = "vst", nfeatures = 2000)

# 查看高变基因中排名前十的基因
top10 <- head(VariableFeatures(pbmc), 10)

# 画图展示：横轴是基因平均表达量，纵轴是方差，红色点是选中的高变基因
plot1 <- VariableFeaturePlot(pbmc)
plot2 <- LabelPoints(plot = plot1, points = top10, repel = TRUE)
plot1 + plot2

# ---------------- 第 10 段：数据缩放（Scaling） ----------------
# 不同基因的表达量量级差异巨大（GAPDH 可能是几万，转录因子可能只有几）。
# 如果不做缩放，PCA 会被高表达基因（如 housekeeping 基因）绑架。
# ScaleData() 对每个基因做 Z-score 标准化：减去均值，除以标准差。
# 这样所有基因都变成均值为 0、标准差为 1 的分布，PCA 时每个基因权重相同。
pbmc <- ScaleData(pbmc)

# ---------------- 第 11 段：PCA 线性降维 ----------------
# PCA（主成分分析）把高维基因表达数据投影到低维空间，
# 保留数据中变化最大的方向，同时去除噪音。
# features = VariableFeatures(object = pbmc) 表示只用之前选出的 2000 个高变基因做 PCA。
pbmc <- RunPCA(pbmc, features = VariableFeatures(object = pbmc))

# 查看每个主成分（PC）Top 基因，帮助理解每个 PC 代表的生物学意义
print(pbmc[["pca"]], dims = 1:5, nfeatures = 5)

# ---------------- 第 12 段：PCA 结果可视化 ----------------
# VizDimLoadings() 展示前两个 PC 中贡献最大的基因（-loading 表示方向相反的表达模式）。
# DimPlot() 把细胞按 PCA 坐标画成散点图，颜色按细胞来源（这里是同一个样本，所以都一个颜色）。
# DimHeatmap() 画热图，展示前几个 PC 中基因表达模式。
VizDimLoadings(pbmc, dims = 1:2, reduction = "pca")
DimPlot(pbmc, reduction = "pca")
DimHeatmap(pbmc, dims = 1, cells = 500, balanced = TRUE)
DimHeatmap(pbmc, dims = 1:15, cells = 500, balanced = TRUE)

# ---------------- 第 13 段：确定用多少个 PC 做后续分析 ----------------
# 不是所有 PC 都有生物学意义，后面的 PC 可能只是噪音。
# ElbowPlot() 画"肘部图"，看每个 PC 能解释多少方差。
# 通常选择拐点（elbow）之前的 PC 数量。对于 PBMC3k，官方教程建议用前 10 个 PC。
ElbowPlot(pbmc, ndims = 50)

# ---------------- 第 14 段：非线性降维 UMAP ----------------
# PCA 是线性降维，适合去噪音、抓主要变化方向；
# UMAP 是非线性降维，擅长把高维中"相似"的细胞在二维平面上放得近，方便肉眼分群。
# dims = 1:10 表示用 PCA 的前 10 个主成分作为输入。
# 注意：UMAP 只用于可视化，不要基于 UMAP 坐标做统计推断。
pbmc <- RunUMAP(pbmc, dims = 1:10)

# ---------------- 第 15 段：构建细胞相似性图谱（聚类前置步骤） ----------------
# FindNeighbors() 基于 PCA 前 10 个维度计算细胞之间的"距离"，
# 把每个细胞与它最相似的细胞连起来，构建一个"相似性图"（SNN 图）。
# 这个图是后续聚类的基础。
pbmc <- FindNeighbors(pbmc, dims = 1:10)

# ---------------- 第 16 段：图聚类（Graph-based Clustering） ----------------
# FindClusters() 在 SNN 图上用 Louvain 算法找社区（cluster）。
# resolution 是聚类分辨率：
#   - 值越大，cluster 越多、越细碎（适合找亚群）。
#   - 值越小，cluster 越少、越粗（适合找大类）。
# 默认 resolution = 0.8，官方教程用 0.5 得到 9 个群，适合 PBMC 这种免疫细胞混合样本。
pbmc <- FindClusters(pbmc, resolution = 0.5)

# 查看每个 cluster 有多少个细胞
table(pbmc$seurat_clusters)

# ---------------- 第 17 段：聚类结果可视化 ----------------
# DimPlot() 在 UMAP 图上按 cluster 着色，label = TRUE 在每个 cluster 旁边标上编号。
# 图中每一团"云"就是一个细胞群，通常对应一种细胞类型。
DimPlot(pbmc, reduction = "umap", label = TRUE)

# ---------------- 第 18 段：找每个 Cluster 的 Marker 基因 ----------------
# FindAllMarkers() 对每个 cluster，找出在该 cluster 中高表达、在其他 cluster 中低表达的基因。
# 这些基因就是该 cluster 的"身份标识"（marker），可用来推断细胞类型。
# only.pos = TRUE：只找在该 cluster 中上调的基因。
# min.pct = 0.25：只考虑在该 cluster 中 ≥25% 细胞表达的基因，去掉极稀有表达的噪音。
# logfc.threshold = 0.25：只保留表达量差异（log fold change）> 0.25 的基因。
pbmc.markers <- FindAllMarkers(pbmc, only.pos = TRUE, min.pct = 0.25, logfc.threshold = 0.25)

# 从每个 cluster 中挑出 avg_log2FC 最大的前 10 个基因
pbmc.markers %>%
  group_by(cluster) %>%
  slice_max(n = 10, order_by = avg_log2FC)

# ---------------- 第 19 段：查看每个 Cluster 最特异的 Marker ----------------
# 先看每个 cluster 中 avg_log2FC 最大的那个基因（每个群的代表性 marker）。
# 然后我们可以根据已知的免疫学知识推断 cluster 的身份：
#   IL7R, CD3D        → CD4 T cell
#   CD79A, MS4A1      → B cell
#   CD14, LYZ         → CD14+ Monocyte
#   MS4A7, CD68       → FCGR3A+ Monocyte
#   GNLY, NKG7        → NK cell
#   FCER1A, CST3      → Dendritic cell
#   PPBP              → Megakaryocyte（血小板前体）

# ---------------- 第 20 段：可视化 Marker 基因表达 ----------------
# FeaturePlot() 在 UMAP 图上用颜色深浅展示特定基因的表达量，
# 黄色/红色 = 高表达，灰色/紫色 = 低表达。
# 这是验证 cluster 身份最直接的方法。
FeaturePlot(pbmc, features = c("MS4A1", "CD79A", "LYZ", "PPBP",
                                "CD14", "IL7R", "CD3D", "GNLY",
                                "NKG7", "FCER1A", "CST3"))

# VlnPlot() 画小提琴图，展示基因在每个 cluster 中的表达分布。
VlnPlot(pbmc, features = c("IL7R", "MS4A1", "CD14", "LYZ", "PPBP", "GNLY"))

# ---------------- 第 21 段：根据 Marker 基因给 Cluster 命名 ----------------
# 利用已知的 marker 基因，我们把数字 cluster 编号替换为细胞类型名称，
# 这样图上显示的就是真正的细胞类型而不是抽象的数字。
new.cluster.ids <- c("Naive CD4 T", "CD14+ Mono", "Memory CD4 T", "B", "CD8 T",
                      "FCGR3A+ Mono", "NK", "DC", "Platelet")
names(new.cluster.ids) <- levels(pbmc)
pbmc <- RenameIdents(pbmc, new.cluster.ids)

# 重新画图，现在 label 显示的是细胞类型名称
DimPlot(pbmc, reduction = "umap", label = TRUE, pt.size = 0.5) + NoLegend()

# ---------------- 第 22 段：保存分析结果 ----------------
# saveRDS() 把整个 Seurat 对象保存为 .rds 文件，
# 下次可以直接 readRDS() 读回来，不需要重新跑 PCA、UMAP 等耗时的步骤。
# 也方便把分析结果传给导师或合作者。
saveRDS(pbmc, file = "pbmc3k_tutorial.rds")
message("分析完成！结果已保存到 pbmc3k_tutorial.rds")
