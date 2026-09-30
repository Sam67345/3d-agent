# =============================================================================
# 06_infercnv_malignant.R
# 进阶模块三【可选 · 高阶】：inferCNV 推断拷贝数变异，区分恶性与正常细胞
# =============================================================================
#
# ⚠️【适用范围声明（请先读）】
#   inferCNV 的用途：从单细胞表达数据推断基因组拷贝数变异（CNV），
#   用来区分"恶性上皮细胞"（带大量 CNV）和"正常细胞"（基因组拷贝数正常）。
#   但 GSE108989 是分选富集的免疫细胞，几乎不含上皮细胞 ——
#   因此本脚本在主项目数据上【没有合适的分析对象】。
#   本模块适用于：换用【含上皮细胞的全细胞类型数据集】时启用，
#   典型选择是 GSE132465（SMC 结直肠癌队列，23 例患者，含上皮、
#   成纤维、内皮、免疫全细胞，单细胞分选自肿瘤与癌旁）。
#   本脚本以 GSE132465 为例写成"改路径即可跑"的完整代码。
#
# 【本模块在讲什么】
#   肿瘤细胞积累大量基因组异常（染色体片段的扩增/缺失）。表达层面上，
#   某段染色体扩增 → 该区域基因整体表达偏高；缺失 → 整体偏低。
#   inferCNV 把基因按染色体位置排序，用滑动窗口平滑表达量，
#   与"参考正常细胞"比较，画出全基因组 CNV 热图。
#   热图上出现成片的染色体臂级红/蓝条纹的细胞群 = 疑似恶性细胞。
#   这是肿瘤单细胞论文里鉴定 malignant cells 的标准方法。
#
# 【输入】
#   含上皮细胞数据集的 Seurat 对象 .rds，或三个原始输入文件（见 1.2）。
#   示例数据集 GSE132465 获取方式：
#     1) 到 GEO 搜索 GSE132465，下载
#        GSE132465_GEO_processed_CRC_10X_raw_UMI_count_matrix.csv.gz
#        以及细胞注释文件 GSE132465_GEO_processed_CRC_10X_cell_annotation.txt.gz；
#     2) 读入后用 read.csv + CreateSeuratObject 构建对象（见下文 1.2 注释代码）；
#     3) 该数据自带作者注释（Cell_type 列含 Epithelial/Myeloid/T/B/Fibroblast 等）。
#   也可以直接读你已经处理好的含上皮细胞的 Seurat .rds（走 1.1 分支）。
#
# 【依赖包及安装命令】
#   install.packages(c("Seurat", "BiocManager", "remotes"))
#   BiocManager::install(c("infercnv", "SingleCellExperiment", "SummarizedExperiment"))
#   # 或 GitHub 最新版：remotes::install_github("broadinstitute/infercnv")
#
# 【版本兼容注意事项】
#   - infercnv 需要表达矩阵是【原始 counts】（整数 UMI），不要用标准化后数据。
#   - Seurat v5：取矩阵用 GetAssayData(obj, layer="counts")，必要时先 JoinLayers。
#   - infercnv 依赖 rjags / JAGS 吗？——不强制。只有 Bayesian 隐马尔可夫
#     模式（HMM=TRUE）才需要额外装 JAGS 软件；本脚本用经典滑动窗口模式
#     （HMM=FALSE），不依赖 JAGS，Windows 上可直接跑。
#
# 【预计运行时间与内存（⚠️ 本模块是三个进阶模块里最重的）】
#   GSE132465 全量约 6.3 万细胞：直接跑会非常慢且吃内存（>16 GB）。
#   强烈建议：① 只取部分患者（如 3~5 例）或② 每类细胞抽样至 1000~3000 个。
#   抽样后（约 5000 细胞）运行约 10~30 分钟，内存约 6~10 GB。
#   本脚本默认开启抽样，参数见第 2 步 N_SAMPLE_PER_GROUP。
#
# 【输出】
#   output/infercnv_run/            —— inferCNV 全部中间文件与最终热图
#   figures/infercnv_heatmap.png    —— CNV 热图（由 infercnv 自动生成，此处仅提示路径）
#
# 注：本脚本为纯代码交付，未实际执行语法检查。
# =============================================================================


# ---- 0. 加载包与设置 ----------------------------------------------------------

suppressPackageStartupMessages({
  library(Seurat)
  library(infercnv)
  library(dplyr)
})

dir.create("figures", showWarnings = FALSE, recursive = TRUE)
dir.create("output/infercnv_run", showWarnings = FALSE, recursive = TRUE)

# ↓↓↓ 两类细胞群名称：按数据实际注释修改 ↓↓↓
# 参考（正常）细胞：基因组稳定、不会有肿瘤 CNV 的细胞类型。
#   经典选择：免疫细胞（T/B/髓系）。同一病人的免疫细胞是最佳内参。
REF_GROUPS   <- c("T cells", "B cells", "Myeloid")
# 观察细胞：疑似恶性的上皮细胞。
OBS_GROUPS   <- c("Epithelial")
# 每类细胞最多抽样多少个（控制内存的关键）
N_SAMPLE_PER_GROUP <- 1500

set.seed(42)  # 固定随机种子，抽样结果可重复


# ---- 1. 准备表达矩阵与细胞注释 ------------------------------------------------

# 【方式 1.1】读已经处理好的 Seurat 对象（推荐） --------------------------
# 要求 meta.data 有一列细胞类型注释（这里假设叫 "Cell_type"，按实际修改）。
INPUT_RDS <- "GSE132465_seurat.rds"     # ←←← 改成你的文件路径
GROUP_COL <- "Cell_type"                # ←←← 改成你的注释列名

seu <- readRDS(INPUT_RDS)
if (packageVersion("SeuratObject") >= "5.0.0") {
  seu <- tryCatch(JoinLayers(seu), error = function(e) seu)
}

# 【方式 1.2】如果只有 GSE132465 的原始文件，用下面代码先构建对象 ----------
# （取消注释并修改路径后使用）
# counts <- read.csv(gzfile("GSE132465_GEO_processed_CRC_10X_raw_UMI_count_matrix.csv.gz"),
#                    row.names = 1, check.names = FALSE)
# anno   <- read.delim(gzfile("GSE132465_GEO_processed_CRC_10X_cell_annotation.txt.gz"),
#                      row.names = 1)
# seu <- CreateSeuratObject(counts = as.matrix(counts), meta.data = anno)
# GROUP_COL <- "Cell_type"   # 该数据注释列名（以实际读入为准，先 colnames(seu@meta.data) 检查）

# 检查注释列
if (!GROUP_COL %in% colnames(seu@meta.data)) {
  stop("找不到注释列 '", GROUP_COL, "'。可用列：",
       paste(colnames(seu@meta.data), collapse = ", "))
}
message("各细胞类型数量："); print(table(seu@meta.data[[GROUP_COL]]))


# ---- 2. 抽样（控制内存）------------------------------------------------------
#
# 【参考细胞选择逻辑（面试考点）】
#   inferCNV 是"相对"分析：观察组信号 = 观察组表达 − 参考组表达（平滑后）。
#   参考组必须是"基因组正常"的细胞，否则肿瘤细胞的 CNV 会被抵消。
#   最佳实践：用同一数据里的免疫细胞（T/B/髓系）做参考——
#   它们和肿瘤细胞经历完全相同的实验流程，技术性批次互相抵消。
#   注意：成纤维细胞/内皮细胞一般不推荐做参考（间质细胞可能有自己的
#   基因组异常，且表达谱与上皮细胞差太远，背景噪音大）。

cells_use <- seu@meta.data %>%
  tibble::rownames_to_column("cell") %>%
  filter(.data[[GROUP_COL]] %in% c(REF_GROUPS, OBS_GROUPS)) %>%
  group_by(.data[[GROUP_COL]]) %>%
  slice_sample(n = N_SAMPLE_PER_GROUP) %>%
  pull(cell)

message("抽样后用于 inferCNV 的细胞数：", length(cells_use))

counts_mat <- GetAssayData(seu, assay = "RNA", layer = "counts")[, cells_use]
# Seurat v4：GetAssayData(seu, assay = "RNA", slot = "counts")

# inferCNV 需要的细胞分组文件（两列：细胞名 分组名，无表头）
group_file <- "output/infercnv_run/cell_groupings.txt"
write.table(
  data.frame(cell = cells_use,
             group = seu@meta.data[cells_use, GROUP_COL]),
  file = group_file, sep = "\t", quote = FALSE,
  row.names = FALSE, col.names = FALSE
)


# ---- 3. 基因染色体位置文件 ----------------------------------------------------
#
# inferCNV 必须知道每个基因在染色体上的位置（三列：基因 染色体 起止坐标）。
# 从 Bioconductor 注释包自动生成（推荐，hg38 用 EnsDb.Hsapiens.v86；
# 若数据比对的是 hg19，改用 EnsDb.Hsapiens.v75）。
#
# 安装：BiocManager::install("EnsDb.Hsapiens.v86")

gene_order_file <- "output/infercnv_run/gene_order.txt"

if (!file.exists(gene_order_file)) {
  library(EnsDb.Hsapiens.v86)
  library(AnnotationDbi)
  genes_anno <- genes(EnsDb.Hsapiens.v86)
  gene_order <- data.frame(
    gene   = genes_anno$gene_name,
    chr    = paste0("chr", seqnames(genes_anno)),
    start  = start(genes_anno),
    end    = end(genes_anno)
  )
  gene_order <- gene_order[!duplicated(gene_order$gene), ]
  write.table(gene_order, gene_order_file, sep = "\t",
              quote = FALSE, row.names = FALSE, col.names = FALSE)
}


# ---- 4. 创建 inferCNV 对象并运行 ----------------------------------------------

infercnv_obj <- CreateInfercnvObject(
  raw_counts_matrix = as.matrix(counts_mat),
  annotations_file  = group_file,
  gene_order_file   = gene_order_file,
  ref_group_names   = REF_GROUPS   # 参考（正常）细胞
)

# 运行参数说明：
#   cutoff = 0.1      —— 每个基因在"参考细胞中至少 10% 细胞表达"才保留
#                        （10X 数据官方建议 0.1；Smart-seq2 建议 1）
#   HMM = FALSE       —— 用经典滑动窗口模式，不需要 JAGS，速度快很多。
#                        若想输出"CNV 状态预测"（几拷贝），改 TRUE 并装 JAGS。
#   denoise = TRUE    —— 去噪，让热图条纹更清晰
#   cluster_by_groups = TRUE —— 热图按细胞类型分行聚类，便于看"上皮群
#                        是否整体带 CNV"
infercnv_obj <- infercnv::run(
  infercnv_obj,
  cutoff = 0.1,
  out_dir = "output/infercnv_run",
  cluster_by_groups = TRUE,
  denoise = TRUE,
  HMM = FALSE,
  num_threads = 4          # Windows 多线程；内存紧张时改 2
)

# infercnv::run 会在 out_dir 下自动生成最终热图：
#   output/infercnv_run/infercnv.png
# 把它复制到 figures/ 方便集中管理：
final_png <- "output/infercnv_run/infercnv.png"
if (file.exists(final_png)) {
  file.copy(final_png, "figures/infercnv_heatmap.png", overwrite = TRUE)
}

saveRDS(infercnv_obj, "output/infercnv_obj.rds")
message("完成！热图见 output/infercnv_run/infercnv.png 和 figures/infercnv_heatmap.png")


# =============================================================================
# 【生物学解读备忘】
#
# 1. 热图怎么读：
#    - 行 = 细胞（按类型分块：上面是观察组上皮细胞，下面是参考免疫细胞）；
#    - 列 = 基因，按染色体 1→22、X 的位置从左到右排列；
#    - 红 = 拷贝数扩增（表达偏高），蓝 = 缺失（表达偏低）。
#    - 如果上皮细胞块出现成片的、染色体臂级别的红/蓝条纹，
#      而参考免疫细胞块基本"白板"→ 上皮细胞带 CNV → 判定为恶性细胞。
#
# 2. 结直肠癌常见 CNV 模式（可作为对照）：7、8、13、20 号染色体扩增，
#    4、8p、17p、18 号染色体缺失（18q 缺失含 SMAD4 是 CRC 经典事件）。
#    若在热图上看到 18q 蓝色条纹，就是很好的生物学印证。
#
# 3. 诚实声明：
#    - inferCNV 是表达推断，分辨率到染色体臂/大片段级别，
#      不能替代 WES/WGS 的金标准 CNV 检测；
#    - 个别正常细胞（如增殖活跃的细胞）也可能有局部假阳性信号；
#    - 结论表述："表达水平提示疑似恶性"，而非"证明恶性"。
#
# 4. 面试话术（如果用了本模块）：
#    "我用 inferCNV 以肿瘤内浸润免疫细胞为参考，推断上皮细胞的拷贝数
#     变异，热图上上皮群呈现染色体臂级扩增/缺失模式（如 18q 缺失），
#     与结直肠癌已知 CNV 谱一致，从而区分出恶性上皮细胞，
#     为后续恶性 vs 正常上皮的差异分析奠定基础。"
# =============================================================================
