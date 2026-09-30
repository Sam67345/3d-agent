# =============================================================================
# 04_monocle3_trajectory.R
# 进阶模块一：Monocle3 拟时序分析（T 细胞分化轨迹）
# =============================================================================
#
# 【本模块在讲什么】
#   主项目（01/02 脚本）把 T 细胞分成了若干亚群（如初始 T、记忆 T、效应 T、
#   耗竭 T）。但"分群"是静态的快照，拟时序分析（pseudotime / trajectory）
#   要回答的是一个动态问题：
#       这些 T 细胞是否沿着"初始/记忆 → 效应 → 耗竭"的方向逐步分化？
#       肿瘤组织里的耗竭 T 细胞是不是分化轨迹的"终点"？
#   这正是结直肠癌 T 细胞单细胞研究（如 Zhang et al., Nature 2018, GSE108989）
#   中的经典分析之一，可以复刻论文中"T 细胞分化轨迹图"。
#
# 【输入】
#   主项目 01 脚本输出的 Seurat 对象 .rds 文件（已完成质控、标准化、聚类、
#   UMAP 和细胞类型注释），例如：
#       ../output/seurat_annotated.rds
#   请把下面 INPUT_RDS 改成你实际的文件路径。
#
# 【依赖包及安装命令】（只需安装一次；在 R 控制台逐条运行）
#   install.packages(c("Seurat", "SeuratObject", "remotes", "BiocManager"))
#   BiocManager::install(c("BiocGenerics", "DelayedArray", "DelayedMatrixStats",
#                          "limma", "S4Vectors", "SingleCellExperiment",
#                          "SummarizedExperiment", "batchelor"))
#   # monocle3 不在 CRAN，需从 GitHub 安装（cole-trapnell-lab 官方仓库）：
#   remotes::install_github("cole-trapnell-lab/monocle3")
#   # Windows 上若编译报错，请先安装 Rtools（https://cran.r-project.org/bin/windows/Rtools/）
#
# 【版本兼容注意事项（重要！）】
#   1. Seurat v5 vs v4：monocle3 的 as.cell_data_set() 主要适配 Seurat v3/v4 的
#      Assay 结构。Seurat v5 引入了分层 Assay5（如多个 layer：data.1, data.2...），
#      直接转换可能报 "unable to find an inherited method" 或提取出空矩阵。
#      解决办法：转换前先 join 一下 layers（见下文代码注释），
#      或干脆从 counts 矩阵手动构建 cell_data_set（本脚本采用更稳妥的"半手动"方式）。
#   2. monocle3 与新版 Matrix 包偶有二进制不兼容报错（"function '...' not provided
#      by package 'Matrix'"），重装 monocle3 通常可解决。
#   3. monocle3 官方已基本停止维护，若 GitHub 安装失败，可尝试：
#      remotes::install_github("cole-trapnell-lab/monocle3", ref = "develop")
#
# 【预计运行时间与内存】
#   GSE108989 约 1.1 万个 T 细胞：learn_graph + order_cells 约 5~15 分钟，
#   内存峰值约 4~8 GB（视电脑而定，16 GB 内存的笔记本完全够用）。
#
# 【输出】
#   figures/trajectory_umap_pseudotime.png  —— 按拟时序着色的 UMAP
#   figures/trajectory_umap_cluster.png     —— 按细胞亚群着色的轨迹 UMAP
#   output/monocle3_cds.rds                 —— 保存的 cell_data_set 对象
#
# 注：本脚本为纯代码交付，未在 R 环境中实际执行过语法检查，如有个别
#     函数名/参数随版本微调，请按报错信息对照 ?函数名 修改。
# =============================================================================


# ---- 0. 加载包与路径设置 ------------------------------------------------------

suppressPackageStartupMessages({
  library(Seurat)
  library(monocle3)
  library(ggplot2)
})

# 输入：主项目 01 输出的 Seurat 对象（按你的实际路径修改）
INPUT_RDS <- "../output/seurat_annotated.rds"

# 输出目录（不存在则创建）
dir.create("figures", showWarnings = FALSE, recursive = TRUE)
dir.create("output",  showWarnings = FALSE, recursive = TRUE)


# ---- 1. 读取 Seurat 对象 ------------------------------------------------------

seu <- readRDS(INPUT_RDS)
message("Seurat 对象读取完成：", ncol(seu), " 个细胞")

# 【Seurat v5 兼容处理】
# v5 中多个样本的 counts 可能分成多个 layer（counts.1, counts.2...），
# 先合并成一个 layer，否则后面取矩阵会报错或只取到部分细胞。
# 对 v4 对象，JoinLayers 不存在/不需要，用 tryCatch 跳过即可。
if (packageVersion("SeuratObject") >= "5.0.0") {
  seu <- tryCatch(
    JoinLayers(seu),
    error = function(e) { message("JoinLayers 跳过：", e$message); seu }
  )
}


# ---- 2. 从 Seurat 对象转换为 monocle3 的 cell_data_set -----------------------
#
# 有两种做法：
#   做法 A（简单）：as.cell_data_set(seu) —— 一键转换，但对 Seurat v5 兼容性差；
#   做法 B（稳妥，本脚本采用）：手动取表达矩阵 + 细胞注释 + 基因注释，
#           用 new_cell_data_set() 构建，再把 Seurat 的 UMAP 坐标和分群
#           塞回去。这样轨迹图直接画在你熟悉的 UMAP 上，和主项目图风格一致。

# 2.1 表达矩阵（monocle3 要求用原始 counts，不用标准化后的 data）
expr_mat <- GetAssayData(seu, assay = "RNA", layer = "counts")
# 如果上面一行报"layer 参数不存在"，说明你的 Seurat 是 v4，改用：
# expr_mat <- GetAssayData(seu, assay = "RNA", slot = "counts")

# 2.2 细胞注释表（行名必须 = 表达矩阵列名 = 细胞 barcode）
cell_meta <- seu@meta.data

# 2.3 基因注释表（行名必须 = 表达矩阵行名 = 基因名；
#     必须有一列叫 "gene_short_name"，monocle3 内部要用）
gene_meta <- data.frame(
  gene_short_name = rownames(expr_mat),
  row.names = rownames(expr_mat)
)

# 2.4 构建 cell_data_set 对象
cds <- new_cell_data_set(
  expr_mat,
  cell_metadata = cell_meta,
  gene_metadata  = gene_meta
)

# 2.5 把 Seurat 算好的 UMAP 坐标直接交给 monocle3
#     （monocle3 自己跑 UMAP 也行，但会和主项目的 UMAP 不一致，不利于故事连贯）
umap_mat <- Embeddings(seu, "umap")          # 细胞 x 2 的坐标矩阵
colnames(umap_mat) <- c("UMAP_1", "UMAP_2")
reducedDims(cds)$UMAP <- umap_mat[colnames(cds), ]  # 保证细胞顺序对齐

# 2.6 把 Seurat 的聚类结果也带过去（learn_graph 需要用 cluster 信息）
#     monocle3 要求 cluster 存在 colData 里，且 colData(cds)$cluster 不能为空。
#     这里用 seurat_clusters；若你的注释列叫 celltype，也可以用它。
cluster_vec <- seu@meta.data[colnames(cds), "seurat_clusters"]
colData(cds)$cluster <- as.factor(cluster_vec)

# 如果有细胞类型注释列（主项目 02 一般会加 celltype 列），也带上，画图更好看：
if ("celltype" %in% colnames(seu@meta.data)) {
  colData(cds)$celltype <- seu@meta.data[colnames(cds), "celltype"]
}

message("cell_data_set 构建完成")


# ---- 3. 学习轨迹图（learn_graph）---------------------------------------------
#
# learn_graph 会在 UMAP 空间上拟合一条"主图"（principal graph），
# 直观理解：在所有细胞分布的云团中找一条/几条穿过云团中心的"路线"，
# 把细胞的排布近似成一棵树（或一条线）。每个细胞被投影到树上最近的位置。
#
# use_partition = TRUE：允许不同"大群"（partition）各自成树。
#   T 细胞亚群之间通常连续分化，若分出多个 partition 且互不连通，
#   说明亚群间差异太大，连不起来——这也是生物学信息。
cds <- learn_graph(cds, use_partition = TRUE,
                   close_loop = FALSE,      # T 细胞分化一般认为不成环
                   learn_graph_control = list(ncenter = NULL))  # 用默认节点数

# 先画一张按 cluster/celltype 着色的轨迹图，检查轨迹是否连贯
color_by <- if ("celltype" %in% colnames(colData(cds))) "celltype" else "cluster"
p_traj_cluster <- plot_cells(
  cds,
  color_cells_by = color_by,
  label_groups_by_cluster = FALSE,
  label_leaves = FALSE,
  label_branch_points = FALSE,
  cell_size = 0.8
) + ggtitle("Monocle3 trajectory colored by cell type")

ggsave("figures/trajectory_umap_cluster.png", p_traj_cluster,
       width = 8, height = 6, dpi = 300)


# ---- 4. 指定起点并计算拟时序（order_cells）------------------------------------
#
# 【关键概念：拟时序的方向是"人为指定"的】
#   算法本身不知道哪头是"起点"，必须告诉它：哪个细胞群是分化起点。
#   对 T 细胞，生物学上公认的起点是 初始 T 细胞（naive，CDR7/LEF1/TCF7 高）
#   或中央记忆 T 细胞（Tcm）；终点应是耗竭 T 细胞（Tex，PDCD1/HAVCR2/LAG3 高）。
#
# 【交互式 vs 程序化指定起点】
#   官方教程用 order_cells(cds) 启动一个交互界面让你鼠标点起点，
#   但交互界面不适合写进可重复运行的脚本。这里用程序化方式：
#   找到"起点细胞群"里离图根部最近的节点，作为 root。
#
#   下面函数的逻辑：统计每条轨迹"叶子节点/节点"被哪些 cluster 的细胞占据，
#   把起点 cluster 细胞占比最高的节点选为根节点。

# ↓↓↓ 按你数据的实际注释修改：起点细胞群的名称/编号 ↓↓↓
# 如果用 celltype 注释：起点一般是 "Naive" / "Tn" / "CD4-Naive" 之类
# 如果只有 cluster 编号：先跑主项目 02 的 marker 鉴定，确定哪个 cluster 是 naive
ROOT_GROUP <- "Naive"     # ←←← 改成你的起点亚群名或 cluster 编号（如 "0"）

get_earliest_principal_node <- function(cds, root_group, group_col) {
  cell_ids <- colnames(cds)[colData(cds)[[group_col]] == root_group]
  if (length(cell_ids) == 0) {
    stop("没找到起点细胞群 '", root_group, "'，请检查 ROOT_GROUP 与注释列是否匹配。\n",
         "当前 ", group_col, " 的取值有：",
         paste(unique(colData(cds)[[group_col]]), collapse = ", "))
  }
  # 每个细胞在轨迹图上最近的节点
  closest_vertex <- cds@principal_graph_aux[["UMAP"]]$pr_graph_cell_proj_closest_vertex
  closest_vertex <- as.matrix(closest_vertex[colnames(cds), ])
  # 起点细胞最常落在哪个节点 → 选为根节点
  root_node <- names(which.max(table(closest_vertex[cell_ids, ])))
  root_node
}

group_col <- if ("celltype" %in% colnames(colData(cds)) &&
                 ROOT_GROUP %in% colData(cds)$celltype) "celltype" else "cluster"

root_node <- get_earliest_principal_node(cds, ROOT_GROUP, group_col)
message("选定根节点：", root_node)

cds <- order_cells(cds, root_pr_nodes = root_node)


# ---- 5. 按拟时序着色画 UMAP ---------------------------------------------------
#
# 图的含义：颜色越"年轻"（蓝/浅）→ 越接近起点（naive）；
#          颜色越"老"（红/深）→ 分化程度越高（预期落在耗竭 T 细胞区域）。
# 这正是论文中常见的 "pseudotime UMAP"。

p_pseudo <- plot_cells(
  cds,
  color_cells_by = "pseudotime",
  label_cell_groups = FALSE,
  label_leaves = FALSE,
  label_branch_points = FALSE,
  graph_label_size = 1.5,
  cell_size = 0.8
) + ggtitle("Monocle3 pseudotime on UMAP")

ggsave("figures/trajectory_umap_pseudotime.png", p_pseudo,
       width = 8, height = 6, dpi = 300)

# 保存对象，方便后续（比如画基因随拟时序变化的曲线）不用再跑一遍
saveRDS(cds, "output/monocle3_cds.rds")
message("完成！图已保存到 figures/，对象保存到 output/monocle3_cds.rds")


# =============================================================================
# 【生物学解读备忘】
#
# 1. 如果轨迹方向反了（耗竭细胞拟时序最小、naive 最大）怎么办？
#    → 说明 ROOT_GROUP 选反了。两种修法：
#      a) 把 ROOT_GROUP 改成 naive/记忆亚群的名字重跑 order_cells；
#      b) 如果只是画图展示，可对 pseudotime 取负（max(pt) - pt）再画，
#         但论文中更推荐做法 a，逻辑更干净。
#
# 2. 预期结果（对照 Zhang et al. 2018 Nature 的 GSE108989 分析）：
#    - CD8 T 细胞：naive → 效应/记忆 → 耗竭（Tex）轨迹，肿瘤组织中
#      耗竭末端富集；
#    - CD4 T 细胞：naive → Tfh/Treg 方向分化；
#    - 若肿瘤 vs 癌旁组织分开着色，可看到肿瘤来源细胞更多分布在
#      拟时序末端（耗竭端），支持"肿瘤微环境驱动 T 细胞耗竭"的故事。
#
# 3. 拟时序是"计算推断"，不是真实时间。表述时要说"分化轨迹/拟时序"，
#    不能说"时间先后顺序被实验证明"。面试时这一点是加分项。
#
# 4. 进阶可选（本脚本未展开）：用 graph_test() 找随拟时序显著变化的基因，
#    用 plot_genes_in_pseudotime() 画 PDCD1/HAVCR2 等耗竭基因沿轨迹
#    的表达曲线，可作为论文风格补充图。
# =============================================================================
