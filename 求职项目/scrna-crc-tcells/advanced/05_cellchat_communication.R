# =============================================================================
# 05_cellchat_communication.R
# 进阶模块二：CellChat 细胞间通讯分析
# =============================================================================
#
# 【本模块在讲什么】
#   细胞不单打独斗：肿瘤细胞、髓系细胞、成纤维细胞和 T 细胞之间通过
#   "配体-受体"（ligand-receptor, L-R）信号互相影响。比如肿瘤/髓系细胞
#   释放的 MIF、TGF-β，或通过 TIGIT、PD-1 等免疫检查点通路抑制 T 细胞。
#   CellChat 用已知的配体-受体数据库（CellChatDB），结合各细胞群的基因
#   表达量，推断"哪类细胞在给哪类细胞发信号、发的什么信号"。
#   可复刻论文中的 "cell-cell communication network" 图。
#
# 【⚠️ 重要前置说明：本模块对数据的要求】
#   细胞通讯分析的前提是数据里要有"多种互相通讯的细胞类型"。
#   GSE108989 是流式分选（FACS）富集的免疫细胞，主体是 T 细胞——
#   如果你的 Seurat 对象里只有 T 细胞各亚群，跑出来的"通讯"只有
#   T↔T，生物学上很单薄，论文里也不会这样做。
#   【解决方案】（注释形式，按需启用）：
#     方案 A（推荐）：换用/合并含全细胞类型的数据。GSE108989 本身包含
#       CD45+ 全免疫细胞，其中部分样本有髓系/B 细胞；更常用的做法是
#       合并 GSE108989（T 细胞）与同一研究的 E 细胞数据（GEO 编号
#       GSE108989 的 E-MTAB 伴随数据）或换用 GSE132465（韩国队列
#       SMC 数据集，含上皮、基质、免疫全细胞，23 例结直肠癌），
#       用其注释好的全细胞对象做 CellChat。
#     方案 B：只做"T 细胞亚群间通讯"，把每个 T 亚群当作一个 cell group
#       跑 CellChat，图中讨论 Tex/Treg 与其他亚群间的信号——可作为
#       演示，但结论要保守。
#     本脚本默认读取"含多种细胞类型注释的 Seurat 对象"；若你的对象
#     只有 T 细胞，它会照样运行（即方案 B），解读时注意局限。
#
# 【输入】
#   主项目 01 输出的 Seurat 对象 .rds（需含细胞类型注释列，默认取
#   meta.data 里的 "celltype" 列；如列名不同请改 GROUP_COL）。
#       INPUT_RDS <- "../output/seurat_annotated.rds"
#
# 【依赖包及安装命令】（只需安装一次）
#   install.packages(c("Seurat", "BiocManager", "remotes", "NMF", "circlize"))
#   BiocManager::install(c("ComplexHeatmap", "BiocNeighbors"))
#   # CellChat 不在 CRAN，从 GitHub 安装（v2 版本，兼容 Seurat v5）：
#   remotes::install_github("jinworks/CellChat")
#   # 注意：旧仓库 sqjin/CellChat 是 v1，与新 Seurat v5 对象有兼容问题，
#   #       请安装 jinworks/CellChat（v2）。
#   # Windows 若提示编译失败，先装 Rtools；NMF 安装慢属正常现象。
#
# 【预计运行时间与内存】
#   ~1 万个细胞、10 个左右细胞群：identifyOverExpressedGenes/Interactions
#   约 2~5 分钟；computeCommunProb（不抽样 raw.use）约 5~15 分钟；
#   内存峰值约 4~8 GB。细胞超过 3 万建议设 population.size = TRUE 并考虑
#   抽样（本脚本已用适合小数据的设置）。
#
# 【输出】
#   figures/cellchat_circle_count.png    —— 互作数量网络图（circle plot）
#   figures/cellchat_circle_weight.png   —— 互作强度网络图
#   figures/cellchat_bubble.png          —— 显著配体-受体对气泡图
#   figures/cellchat_pathway_MIF.png     —— 重点通路（MIF）网络图
#   output/cellchat_object.rds           —— CellChat 对象
#
# 注：本脚本为纯代码交付，未实际执行语法检查。
# =============================================================================


# ---- 0. 加载包与设置 ----------------------------------------------------------

suppressPackageStartupMessages({
  library(Seurat)
  library(CellChat)
  library(ggplot2)
})

INPUT_RDS <- "../output/seurat_annotated.rds"
GROUP_COL <- "celltype"          # 细胞类型注释所在列名（按你的对象修改）

dir.create("figures", showWarnings = FALSE, recursive = TRUE)
dir.create("output",  showWarnings = FALSE, recursive = TRUE)


# ---- 1. 读取对象并准备输入 ----------------------------------------------------

seu <- readRDS(INPUT_RDS)

if (!GROUP_COL %in% colnames(seu@meta.data)) {
  stop("meta.data 里找不到注释列 '", GROUP_COL,
       "'。可用的列有：", paste(colnames(seu@meta.data), collapse = ", "),
       "\n请修改 GROUP_COL。")
}

# CellChat 输入①：标准化表达矩阵（log-normalized data，不是 counts）
data.input <- GetAssayData(seu, assay = "RNA", layer = "data")
# Seurat v4 请改用：GetAssayData(seu, assay = "RNA", slot = "data")

# CellChat 输入②：细胞分群标签（必须是 factor）
labels <- as.factor(seu@meta.data[[GROUP_COL]])
names(labels) <- colnames(seu)
message("共 ", ncol(seu), " 个细胞，", nlevels(labels), " 个细胞群：")
print(table(labels))


# ---- 2. 构建 CellChat 对象 ----------------------------------------------------
#
# 用 data + labels 创建（不直接传 Seurat 对象，避免 v5 结构兼容问题；
# 这也是官方推荐的"从矩阵创建"方式）。
cellchat <- createCellChat(object = data.input,
                           meta = data.frame(labels = labels,
                                             row.names = names(labels)),
                           group.by = "labels")

# 设置配体-受体数据库：
#   人用 CellChatDB.human；小鼠用 CellChatDB.mouse。
#   "Secreted Signaling" 只看分泌信号，适合先做；
#   想包含膜结合互作（如 PD-1/PD-L1、TIGIT/PVR 这类检查点分子，
#   它们是 cell-cell contact），要用 "Cell-Cell Contact" 或合并库。
#   → 为覆盖免疫检查点通路，本脚本用非蛋白-蛋白互作剔除后的"全部人工整理库"。
CellChatDB <- CellChatDB.human

# 合并分泌信号 + 细胞接触 + ECM，覆盖最全（检查点分子多在 contact 类）
CellChatDB.use <- CellChatDB  # 也可改成 subsetDB(CellChatDB, search = "Cell-Cell Contact")
cellchat@DB <- CellChatDB.use


# ---- 3. 计算通讯概率 ----------------------------------------------------------
#
# 步骤逻辑（对应 CellChat 论文的三步）：
#   ① 找每个细胞群里"过表达"的配体和受体基因；
#   ② 找过表达的配体-受体对；
#   ③ 用质量作用定律 + 群平均表达量计算每个 L-R 对的通讯概率，
#      再做置换检验得到 p 值。

cellchat <- subsetData(cellchat)   # 把信号基因子集提取出来，加速后续计算

cellchat <- identifyOverExpressedGenes(cellchat)
cellchat <- identifyOverExpressedInteractions(cellchat)

# 计算通讯概率。
#   population.size = FALSE：认为各群细胞数已反映真实比例
#     （FACS 分选数据比例被人为改变，建议 FALSE；全组织消化数据可设 TRUE，
#      GSE108989 是分选数据 → 用 FALSE）。
#   raw.use = TRUE（默认）：用全部细胞算群平均，小数据集更稳。
cellchat <- computeCommunProb(cellchat, population.size = FALSE)

# 过滤掉"在太少细胞里表达"的通讯（默认每群至少 10 个细胞才纳入）
cellchat <- filterCommunication(cellchat, min.cells = 10)

# 把 L-R 水平的结果汇总到"信号通路"水平（如 MIF、TIGIT、PD-L1 等通路名）
cellchat <- computeCommunProbPathway(cellchat)

# 聚合网络：得到任意两群之间的总互作次数和总强度
cellchat <- aggregateNet(cellchat)

message("通讯概率计算完成")


# ---- 4. 图一：聚合网络 circle plot --------------------------------------------
#
# 圆圈 = 细胞群，圆的大小 ∝ 该群参与的互作总数；
# 连线粗细 ∝ 两群间互作数量（或强度）；箭头方向 = 信号发送方 → 接收方。
# 解读：肿瘤/髓系群若有"粗箭头"指向 Tex（耗竭 T 细胞），
# 说明微环境在向耗竭 T 细胞大量发送信号——这是论文核心叙事之一。

groupSize <- as.numeric(table(labels))

png("figures/cellchat_circle_count.png", width = 2000, height = 2000, res = 300)
netVisual_circle(cellchat@net$count,
                 vertex.weight = groupSize,
                 weight.scale = TRUE,
                 label.edge = FALSE,
                 title.name = "Number of interactions")
dev.off()

png("figures/cellchat_circle_weight.png", width = 2000, height = 2000, res = 300)
netVisual_circle(cellchat@net$weight,
                 vertex.weight = groupSize,
                 weight.scale = TRUE,
                 label.edge = FALSE,
                 title.name = "Interaction weights/strength")
dev.off()


# ---- 5. 图二：显著配体-受体对气泡图 -------------------------------------------
#
# 每一行 = 一个 L-R 对，每一列 = 一对"发送群 → 接收群"；
# 气泡颜色 = 通讯概率（强度），气泡大小 = 显著性（p 值）。
# 用法：找"指向耗竭 T 细胞 / Treg"的强信号 L-R 对，写进结果解读。

p_bubble <- netVisual_bubble(
  cellchat,
  signaling = NULL,          # NULL = 展示所有显著 L-R 对；可指定 c("MIF","TIGIT")
  remove.isolate = TRUE      # 去掉没有任何显著互作的群对组合
)
ggsave("figures/cellchat_bubble.png", p_bubble,
       width = 12, height = 10, dpi = 300, limitsize = FALSE)


# ---- 6. 图三：重点通路单独网络图（MIF 为例）------------------------------------
#
# 为什么挑 MIF：MIF–CD74 是结直肠癌单细胞研究中反复出现的
# "髓系/肿瘤细胞 → T 细胞"免疫抑制信号（Zhang et al. 多次报道）；
# TIGIT（TIGIT–PVR/NECTIN2）是 T 细胞耗竭相关检查点通路。
# 这两个通路的图放在一起，就是一个完整的"免疫抑制微环境"故事。

pathways.show <- cellchat@netP$pathways
message("所有显著通路：", paste(pathways.show, collapse = ", "))

focus_pathway <- if ("MIF" %in% pathways.show) "MIF" else pathways.show[1]

png(paste0("figures/cellchat_pathway_", focus_pathway, ".png"),
    width = 2000, height = 2000, res = 300)
netVisual_aggregate(cellchat,
                    signaling = focus_pathway,
                    layout = "circle",
                    vertex.weight = groupSize)
dev.off()

# 如果 TIGIT 显著，单独再画一张（检查点通路，面试高频考点）
if ("TIGIT" %in% pathways.show) {
  png("figures/cellchat_pathway_TIGIT.png", width = 2000, height = 2000, res = 300)
  netVisual_aggregate(cellchat, signaling = "TIGIT",
                      layout = "circle", vertex.weight = groupSize)
  dev.off()
}

# 每个群在各通路中的"角色"（发送者/接收者/中介/影响者）——进阶图，可选
# cellchat <- netAnalysis_computeCentrality(cellchat)
# netAnalysis_signalingRole_network(cellchat, signaling = focus_pathway)


# ---- 7. 保存 ------------------------------------------------------------------

saveRDS(cellchat, "output/cellchat_object.rds")

# 导出显著 L-R 结果表，方便写报告/筛选
df.net <- subsetCommunication(cellchat)
write.csv(df.net, "output/cellchat_significant_LR.csv", row.names = FALSE)

message("完成！图在 figures/，对象和结果表在 output/")


# =============================================================================
# 【生物学解读备忘】
#
# 1. 结果三要素（面试回答框架）：
#    谁发信号（sender）→ 谁收信号（receiver）→ 通过什么通路（pathway/L-R）。
#    例："髓系细胞通过 MIF–CD74 向 CD8+ 耗竭 T 细胞发信号，
#        且 TIGIT–PVR 互作在肿瘤组显著增强，提示检查点介导的免疫抑制。"
#
# 2. 注意事项（诚实声明，面试加分）：
#    - CellChat 推断的是"表达量支持的潜在通讯"，不是实验证明的通讯；
#    - 配体和受体"共表达"不等于物理上真的接触；
#    - 若数据只有 T 细胞（方案 B），只能讨论 T 细胞亚群间互作，
#      无法得出"微环境抑制 T 细胞"的结论——这是数据限制，不是分析错误。
#
# 3. 分组比较（本脚本未展开，进阶可选）：
#    把肿瘤组织和癌旁组织分别构建 CellChat 对象，再用 mergeCellChat +
#    netVisual_diffInteraction 比较两组网络差异——论文里的标准做法，
#    能直接回答"肿瘤微环境中哪些通讯被增强了"。
# =============================================================================
