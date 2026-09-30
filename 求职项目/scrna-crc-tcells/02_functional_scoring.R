# =============================================================================
# 02_functional_scoring.R
# 任务：细胞毒性评分 & 耗竭评分（AddModuleScore）-> 亚群间比较 ->
#       亚群间差异表达 -> clusterProfiler GO/KEGG 富集
# 输入：results/seu_annotated.rds（脚本 01 的输出，必须先跑通 01）
# 科学背景：结直肠癌肿瘤微环境中，浸润 CD8 T 细胞常处于"耗竭"状态
#   （exhaustion）：持续抗原刺激导致 PD-1/TIM-3 等抑制性受体上调、
#   杀伤功能下降。本脚本定量比较各亚群的细胞毒性 vs 耗竭程度。
# =============================================================================

suppressPackageStartupMessages({
  library(Seurat)
  library(dplyr)
  library(ggplot2)
  library(clusterProfiler)   # GO/KEGG 富集分析
  library(org.Hs.eg.db)      # 人类基因 ID 注释数据库
})

set.seed(42)
dir.create("results", showWarnings = FALSE)

if (!file.exists("results/seu_annotated.rds")) {
  stop("找不到 results/seu_annotated.rds，请先运行 01_qc_clustering.R")
}
seu <- readRDS("results/seu_annotated.rds")
Idents(seu) <- seu$cell_type   # 确保当前分组是细胞亚型

## ---- 1. 功能基因集评分（AddModuleScore）----------------------------------------
# 细胞毒性（cytotoxicity）基因集：颗粒酶 + 穿孔素 + NK 颗粒蛋白
#   GZMA/GZMB：颗粒酶，进入靶细胞诱导凋亡
#   PRF1：穿孔素，在靶细胞膜上打孔
#   NKG7/GNLY：细胞毒性淋巴细胞颗粒蛋白
cyto_genes <- c("GZMA", "GZMB", "PRF1", "NKG7", "GNLY")

# 耗竭（exhaustion）基因集：抑制性受体 + 耗竭转录因子
#   PDCD1(PD-1)、HAVCR2(TIM-3)、LAG3、TIGIT、CTLA4：经典免疫检查点
#   TOX：耗竭 T 细胞命运的关键转录因子（近年 Nature 系列工作确立）
exhaust_genes <- c("PDCD1", "HAVCR2", "LAG3", "TIGIT", "TOX", "CTLA4")

# AddModuleScore 原理：对每个细胞，计算目标基因集的平均表达，
# 减去"表达水平匹配的对照基因集"的平均表达，得到相对富集分数。
# 这样可校正细胞测序深度差异。ctrl = 100 表示每个目标基因配 100 个对照基因，
# 对照越多分数越稳，但越慢；100 是官方默认、经验足够。
seu <- AddModuleScore(seu, features = list(cyto_genes),    name = "Cytotoxic_Score", ctrl = 100)
seu <- AddModuleScore(seu, features = list(exhaust_genes), name = "Exhaustion_Score", ctrl = 100)
# AddModuleScore 会自动在列名后加序号 1
colnames(seu@meta.data)[colnames(seu@meta.data) == "Cytotoxic_Score1"]  <- "Cytotoxic_Score"
colnames(seu@meta.data)[colnames(seu@meta.data) == "Exhaustion_Score1"] <- "Exhaustion_Score"

## ---- 2. UMAP 上展示两个评分 -----------------------------------------------------
p_score_umap <- FeaturePlot(seu, features = c("Cytotoxic_Score", "Exhaustion_Score"),
                            ncol = 2, pt.size = 0.3) &
  scale_color_gradientn(colors = c("lightgrey", "blue", "red"))
ggsave("results/UMAP_scores.pdf", p_score_umap, width = 12, height = 5)

## ---- 3. 亚群间评分比较：箱线图 + 统计检验 ---------------------------------------
df_score <- seu@meta.data %>% select(cell_type, Cytotoxic_Score, Exhaustion_Score)

p_box <- df_score %>%
  tidyr::pivot_longer(cols = c(Cytotoxic_Score, Exhaustion_Score),
                      names_to = "score_type", values_to = "score") %>%
  ggplot(aes(x = cell_type, y = score, fill = cell_type)) +
  geom_boxplot(outlier.size = 0.3) +
  facet_wrap(~score_type, scales = "free_y") +
  theme_bw() +
  theme(axis.text.x = element_text(angle = 30, hjust = 1)) +
  ggtitle("Cytotoxicity / Exhaustion scores across T cell subtypes")
ggsave("results/score_boxplot_by_subtype.pdf", p_box, width = 10, height = 5)

# 统计检验：亚群数通常 > 2，用 Kruskal-Wallis（非参数多组比较，
# 不假设正态分布，比 ANOVA 稳健——单细胞评分分布往往偏态）。
# 若整体显著，再做两两 Wilcoxon 比较 + BH 校正。
kw_cyto    <- kruskal.test(Cytotoxic_Score  ~ cell_type, data = df_score)
kw_exhaust <- kruskal.test(Exhaustion_Score ~ cell_type, data = df_score)
cat("Kruskal-Wallis 检验：\n")
cat("  Cytotoxic_Score  p =", format.pval(kw_cyto$p.value), "\n")
cat("  Exhaustion_Score p =", format.pval(kw_exhaust$p.value), "\n")

pair_cyto <- pairwise.wilcox.test(df_score$Cytotoxic_Score,  df_score$cell_type,
                                  p.adjust.method = "BH")
pair_exh  <- pairwise.wilcox.test(df_score$Exhaustion_Score, df_score$cell_type,
                                  p.adjust.method = "BH")
sink("results/score_statistical_tests.txt")
cat("=== Kruskal-Wallis ===\n"); print(kw_cyto); print(kw_exhaust)
cat("\n=== 两两 Wilcoxon + BH 校正（Cytotoxic）===\n"); print(pair_cyto)
cat("\n=== 两两 Wilcoxon + BH 校正（Exhaustion）===\n"); print(pair_exh)
sink()

## ---- 4. 亚群间差异表达 -----------------------------------------------------------
# 重点比较：CD8_T vs Treg（肿瘤免疫中最受关注的一对）。
# 若你的数据里没有这两个标签，请按 01 的实际注释结果修改。
group_a <- "CD8_T"
group_b <- "Treg"

if (all(c(group_a, group_b) %in% unique(seu$cell_type))) {
  de <- FindMarkers(seu, ident.1 = group_a, ident.2 = group_b,
                    test.use = "wilcox",           # 单细胞差异表达最常用、最稳健
                    min.pct = 0.1,                 # 至少在 10% 细胞表达才检验
                    logfc.threshold = 0.25)
  de$gene <- rownames(de)
  de <- de %>% arrange(p_val_adj)                  # p_val_adj 是 BH 校正后的 FDR
  write.csv(de, paste0("results/DE_", group_a, "_vs_", group_b, ".csv"),
            row.names = FALSE)

  # 火山图
  de <- de %>% mutate(sig = case_when(
    p_val_adj < 0.05 & avg_log2FC >  0.25 ~ paste0("Up in ", group_a),
    p_val_adj < 0.05 & avg_log2FC < -0.25 ~ paste0("Up in ", group_b),
    TRUE ~ "NS"))
  p_volcano <- ggplot(de, aes(x = avg_log2FC, y = -log10(p_val_adj + 1e-300),
                              color = sig)) +
    geom_point(size = 0.5, alpha = 0.6) +
    scale_color_manual(values = c("red", "blue", "grey")) +
    theme_bw() +
    ggtitle(paste0(group_a, " vs ", group_b, " differential expression"))
  ggsave("results/DE_volcano.pdf", p_volcano, width = 7, height = 6)
} else {
  warning("数据中缺少 ", group_a, " 或 ", group_b,
          " 标签，请按 01 的实际注释结果修改 group_a / group_b")
  de <- NULL
}

## ---- 5. GO / KEGG 富集分析 ---------------------------------------------------------
# 取差异基因中 FDR < 0.05 且 log2FC > 0.25 的基因（即 group_a 高表达基因）做富集
if (!is.null(de)) {
  sig_genes <- de %>% filter(p_val_adj < 0.05, avg_log2FC > 0.25) %>% pull(gene)
  cat("用于富集分析的显著上调基因数：", length(sig_genes), "\n")

  if (length(sig_genes) >= 10) {   # 基因太少富集没有意义
    # 基因符号 -> Entrez ID（clusterProfiler 的富集函数要 Entrez ID）
    id_map <- bitr(sig_genes, fromType = "SYMBOL", toType = "ENTREZID",
                   OrgDb = org.Hs.eg.db)

    # GO 富集（Biological Process）。pAdjustMethod = "BH"：多重检验校正，
    # 因为同时检验了上千个 GO 条目，不校正会有大量假阳性。
    ego <- enrichGO(gene          = id_map$ENTREZID,
                    OrgDb         = org.Hs.eg.db,
                    ont           = "BP",
                    pAdjustMethod = "BH",
                    pvalueCutoff  = 0.05,
                    qvalueCutoff  = 0.05,
                    readable      = TRUE)   # 结果里把 Entrez ID 翻回基因名，好读

    # KEGG 通路富集
    ekegg <- enrichKEGG(gene          = id_map$ENTREZID,
                        organism      = "hsa",   # human
                        pAdjustMethod = "BH",
                        pvalueCutoff  = 0.05)

    # 气泡图：横轴 GeneRatio（富集到的基因占该通路比例），点大小=基因数，颜色=FDR
    pdf("results/GO_enrichment_dotplot.pdf", width = 9, height = 7)
    print(dotplot(ego, showCategory = 15) + ggtitle("GO BP enrichment"))
    dev.off()
    pdf("results/KEGG_enrichment_dotplot.pdf", width = 9, height = 7)
    print(dotplot(ekegg, showCategory = 15) + ggtitle("KEGG enrichment"))
    dev.off()

    write.csv(as.data.frame(ego),   "results/GO_enrichment.csv",   row.names = FALSE)
    write.csv(as.data.frame(ekegg), "results/KEGG_enrichment.csv", row.names = FALSE)
  } else {
    warning("显著差异基因不足 10 个，跳过富集分析")
  }
}

cat("脚本 02 完成。新增输出：UMAP_scores.pdf, score_boxplot_by_subtype.pdf,\n")
cat("  score_statistical_tests.txt, DE_*.csv, DE_volcano.pdf,\n")
cat("  GO_enrichment_dotplot.pdf, KEGG_enrichment_dotplot.pdf 及对应 csv\n")
