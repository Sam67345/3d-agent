# GSE60450 小鼠乳腺 RNA-seq 完整复现指南

> **目标**：从 SRA 原始数据下载，到 DESeq2 差异表达分析 + 可视化，完整复现 Fu et al. 2015 的 RNA-seq 流程。  
> **适用环境**：WSL2 + Ubuntu（Windows 子系统），Python 较好、R 有基础、Linux 只会基本命令。  
> **数据**：GEO GSE60450 / SRA SRP045534，12 个样本（2 细胞类型 × 3 状态 × 2 重复）。

---

## 📋 实验设计速查

| 样本名 | SRR 编号 | GSM 编号 | 细胞类型 | 发育状态 | 分组 |
|--------|---------|---------|---------|---------|------|
| LV1 | SRR1552444 | GSM1480291 | Luminal | virgin | Luminal_virgin |
| LV2 | SRR1552445 | GSM1480292 | Luminal | virgin | Luminal_virgin |
| LP1 | SRR1552446 | GSM1480293 | Luminal | pregnant | Luminal_pregnant |
| LP2 | SRR1552447 | GSM1480294 | Luminal | pregnant | Luminal_pregnant |
| LL1 | SRR1552448 | GSM1480295 | Luminal | lactating | Luminal_lactating |
| LL2 | SRR1552449 | GSM1480296 | Luminal | lactating | Luminal_lactating |
| BV1 | SRR1552450 | GSM1480297 | Basal | virgin | Basal_virgin |
| BV2 | SRR1552451 | GSM1480298 | Basal | virgin | Basal_virgin |
| BP1 | SRR1552452 | GSM1480299 | Basal | pregnant | Basal_pregnant |
| BP2 | SRR1552453 | GSM1480300 | Basal | pregnant | Basal_pregnant |
| BL1 | SRR1552454 | GSM1480301 | Basal | lactating | Basal_lactating |
| BL2 | SRR1552455 | GSM1480302 | Basal | lactating | Basal_lactating |

**科学问题**：
1. Luminal 和 Basal 两种乳腺细胞在基因表达上有何差异？
2. 怀孕和泌乳状态如何影响每种细胞类型的基因表达？

---

## 📁 文件结构

本项目所有脚本和结果放在以下目录结构中：

```
GSE60450_RNAseq/
├── samplesheet.csv          # 样本信息表
├── run.sh                   # W2: 原始数据处理脚本
├── deseq2_analysis.R        # W3: DESeq2 差异分析脚本
├── visualization.py         # W3: Python 可视化脚本
└── GSE60450_analysis/       # 自动生成的分析目录
    ├── reference/           # 参考基因组 + GTF + 索引
    ├── raw_data/            # SRA 下载的 fastq.gz
    ├── qc/                  # FastQC + MultiQC 报告
    ├── alignment/           # STAR/HISAT2 比对 BAM
    ├── counts/              # featureCounts 计数矩阵
    ├── logs/                # 各步骤运行日志
    └── results/             # DESeq2 结果 + 图表
        ├── figures/
        │   ├── volcano_Luminal_vs_Basal_results.png    ← 火山图
        │   ├── MA_Luminal_vs_Basal_results.png         ← MA 图
        │   ├── PCA_plot.png                             ← PCA 图
        │   ├── top20_DEG_heatmap.pdf                    ← 热图
        │   └── DEG_summary.png                          ← 汇总图
        ├── top50_Luminal_vs_Basal.csv                   ← Top 差异基因表
        └── Luminal_vs_Basal_results.csv                 ← 完整差异分析结果
```

---

## 🗓️ 两周时间分配

### W2 — 原始数据处理（环境 + 比对 + 定量）

| 天数 | 任务 | 预计耗时 | 说明 |
|------|------|---------|------|
| Day 1 | 环境准备 | 30 min | 装 conda + 创建 rnaseq 环境 |
| Day 2 | 参考基因组下载 | 30 min | mm39 基因组 + GTF（约 850MB） |
| Day 3 | 建索引 | 1~2 h | STAR 建索引（内存瓶颈步骤） |
| Day 4~5 | SRA 数据下载 | 2~4 h | 12 个样本，取决于网速 |
| Day 6 | FastQC 质控 | 30 min | 检查数据质量 |
| Day 7 | 比对 + 定量 | 3~5 h | STAR 比对 + featureCounts |

### W3 — 差异分析与可视化

| 天数 | 任务 | 预计耗时 | 说明 |
|------|------|---------|------|
| Day 1~2 | DESeq2 分析 | 1~2 h | R 中读取 counts，跑差异分析 |
| Day 3~4 | Python 可视化 | 1 h | 火山图、MA 图、汇总图 |
| Day 5~7 | 结果解读 + 报告 | - | 理解 Top DEGs 的生物学意义 |

---

## 🔧 W2 详细步骤与命令解释

### 前置检查：WSL2 内存配置

STAR 建索引需要 **约 16~30GB 内存**（取决于 `--genomeSAindexNbases` 参数）。如果你的电脑总内存 < 16GB，请改用 **HISAT2**（只需 ~8GB）。

检查 WSL2 内存分配（在 **Windows PowerShell** 中运行）：

```powershell
# 查看当前 WSL2 配置
cat $env:USERPROFILE\.wslconfig

# 如果文件不存在或内存 < 16GB，创建/编辑配置：
# 记事本打开配置文件
notepad $env:USERPROFILE\.wslconfig
```

写入以下内容（根据你的实际内存调整，预留 4GB 给 Windows）：

```ini
[wsl2]
memory=20GB      # 如果电脑有 32GB 内存，给 WSL 分 20GB
processors=8     # CPU 核心数
swap=4GB
localhostForwarding=true
```

保存后，在 PowerShell 中重启 WSL：

```powershell
wsl --shutdown
# 然后重新打开 WSL2 Ubuntu
```

---

### 0. 项目初始化

```bash
# 把本项目的 run.sh、samplesheet.csv 复制到 WSL2 的一个工作目录
# 推荐放在 /home/你的用户名/ 下，不要放在 /mnt/c/（Windows 盘），IO 会很慢

mkdir -p ~/rnaseq_project
cd ~/rnaseq_project
# 把 run.sh、samplesheet.csv 复制到此处

# 给脚本执行权限
chmod +x run.sh
```

---

### 1. 环境准备（Step 1）

```bash
./run.sh --step 1
```

**这段命令做了什么？**

| 工具 | 作用 |
|------|------|
| `conda` | Python 包/环境管理器，也是生物信息学软件分发的主流平台 |
| `sra-tools` | NCBI 官方工具，包含 `prefetch`（下载 SRA 数据）和 `fasterq-dump`（转 fastq） |
| `fastqc` | 质控工具，检查碱基质量、GC 含量、接头污染等 |
| `multiqc` | 把多个 FastQC 报告汇总成一个 HTML 网页 |
| `star` | 比对工具，RNA-seq 金标准，对剪接位点敏感 |
| `hisat2` | 备选比对工具，内存需求低 |
| `samtools` | BAM/SAM 文件操作（排序、索引、格式转换） |
| `subread` | 包含 `featureCounts`，用于基因定量 |
| `pigz` | 并行 gzip，比系统 gzip 快 4~8 倍 |

---

### 2. 下载参考基因组（Step 2）

```bash
./run.sh --step 2
```

**关键参数解释：**

- **GENCODE M33** = 小鼠 mm39 (GRCm39) 的注释版本，比 UCSC 或 NCBI 更完整
- **primary_assembly**: 只包含主要染色体（1~19, X, Y, MT），不包含 unplaced/unlocalized contigs，减少比对噪音
- **`.fa.gz`**: FASTA 格式的基因组序列（压缩后约 800MB）
- **`.gtf.gz`**: Gene Transfer Format，基因注释文件（包含每个基因、外显子、UTR 的坐标）

---

### 3. 建索引（Step 3）

```bash
# STAR 方式（内存需求高）
./run.sh --step 3 --aligner star

# HISAT2 方式（内存需求低，备选）
./run.sh --step 3 --aligner hisat2
```

**STAR 建索引参数详解：**

| 参数 | 含义 | 为什么重要 |
|------|------|-----------|
| `--runMode genomeGenerate` | 告诉 STAR 这次不是比对，是建索引 | 必填 |
| `--genomeDir` | 索引输出目录 | 后续比对时要用 |
| `--genomeFastaFiles` | 输入基因组 FASTA | 参考序列来源 |
| `--sjdbGTFfile` | 输入 GTF | STAR 据此构建剪接位点数据库，RNA-seq 必须有 |
| `--sjdbOverhang 49` | `read 长度 - 1` | 该数据 read 约 50bp，所以设 49 |
| `--genomeSAindexNbases` | 控制后缀数组大小 | 默认 14（~30GB RAM）；内存不够时降到 11（~16GB） |

---

### 4. SRA 数据下载（Step 4）

```bash
./run.sh --step 4
```

**下载流程：**

```
SRR 编号 ──[prefetch]──> .sra 文件 ──[fasterq-dump]──> .fastq ──[pigz]──> .fastq.gz
```

| 命令 | 作用 |
|------|------|
| `prefetch SRRxxxx` | 从 NCBI SRA 数据库下载原始数据到本地缓存 |
| `fasterq-dump` | 把 .sra 解码成 .fastq（比旧版 `fastq-dump` 快 3~5 倍） |
| `--split-files` | 双端测序拆成 `_1.fastq` 和 `_2.fastq`；单端不受影响 |
| `pigz -p ${THREADS}` | 多线程并行压缩 fastq，减少磁盘占用（约 70%） |

⚠️ **注意**：12 个样本总数据量约 **12~20GB**（压缩后），请确保硬盘有足够空间。

---

### 5. FastQC 质控（Step 5）

```bash
./run.sh --step 5
```

**FastQC 报告重点看这几项：**

| 模块 | 合格标准 | 常见问题 |
|------|---------|---------|
| Per base sequence quality | 大部分碱基在绿区（Q > 30） | 3' 端质量下降 → 需要截短（Trimmomatic） |
| Per sequence GC content | 峰值与理论 GC 一致 | 多峰 → 可能存在污染 |
| Adapter Content | 无显著接头序列 | 有接头 → 需要去接头（cutadapt） |
| Sequence Duplication | < 50% | 过高 → PCR 过度扩增 |

> **GSE60450 是已发表的成熟数据**，通常质控直接通过，不需要 Trimmomatic 修剪。如果 FastQC 报告有问题，再告诉我，我会加上修剪步骤。

---

### 6. 比对（Step 6）

```bash
./run.sh --step 6
```

**STAR 比对参数详解：**

| 参数 | 含义 |
|------|------|
| `--genomeDir` | 索引目录（Step 3 的输出） |
| `--readFilesIn` | 输入 fastq（单端一个文件，双端两个文件） |
| `--readFilesCommand zcat` | 自动解压 .gz 文件 |
| `--outSAMtype BAM SortedByCoordinate` | 输出已按基因组坐标排序的 BAM |
| `--outFilterMultimapNmax 20` | 一个 read 最多允许比对到 20 个位置，超过则丢弃 |
| `--outFilterMismatchNoverReadLmax 0.04` | 错配率 < 4%（50bp read 最多 2 个错配） |
| `--alignIntronMin 20 --alignIntronMax 1000000` | 内含子长度范围 20bp ~ 1Mb |
| `--quantMode GeneCounts` | **额外输出**：按基因计数（类似 htseq-count），存为 `ReadsPerGene.out.tab` |

**输出文件：**

| 文件 | 说明 |
|------|------|
| `SRRxxxx_Aligned.sortedByCoord.out.bam` | 排序后的 BAM（主输出） |
| `SRRxxxx_Log.final.out` | 比对统计摘要（map rate、unique map rate 等） |
| `SRRxxxx_ReadsPerGene.out.tab` | STAR 自带的基因计数（featureCounts 的备选） |

---

### 7. BAM 索引（Step 7）

```bash
./run.sh --step 7
```

`samtools index` 生成 `.bai` 文件，是 BAM 的"目录"。没有它，IGV 等可视化工具无法快速定位 BAM 的某个区域。

---

### 8. 基因定量（Step 8）

```bash
./run.sh --step 8
```

**featureCounts 参数详解：**

| 参数 | 含义 |
|------|------|
| `-T ${THREADS}` | 多线程加速 |
| `-a gencode.vM33.gtf.gz` | GTF 注释文件（告诉 featureCounts 基因的外显子坐标） |
| `-g gene_id` | 按 GTF 中的 `gene_id` 字段汇总（如 ENSMUSG00000051951） |
| `-t exon` | 只统计落在 exon 上的 reads |
| `--largestOverlap` | 一个 read 跨多个基因时，分配给重叠最大的那个 |
| `-p --countReadPairs` | 双端测序：把一对 reads 算作 1 次计数 |

**输出格式：**

```
Geneid  Chr Start End  Strand  Length  Sample1  Sample2  ...
ENSMUSG...  1;1;1  3205901;...  3216344;...  +;+;+  1106  152  203  ...
```

- 第 1 列：`Geneid`
- 第 2~6 列：基因坐标信息（DESeq2 不需要）
- 第 7 列起：每个样本的原始计数（raw counts）

脚本中用 `cut -f1,7-` 提取纯计数矩阵，作为 DESeq2 的输入。

---

## 📊 W3 详细步骤与命令解释

### 9. 安装 DESeq2（如果还没装）

```r
# 在 R/RStudio 中运行
if (!requireNamespace("BiocManager", quietly = TRUE))
    install.packages("BiocManager")

BiocManager::install(c("DESeq2", "pheatmap", "RColorBrewer"))
```

---

### 10. DESeq2 差异分析

```bash
# 在 WSL2 终端中运行
Rscript deseq2_analysis.R
```

或在 RStudio 中打开 `deseq2_analysis.R`，逐段运行。

**DESeq2 分析流程解析：**

```
Count Matrix ──[DESeqDataSetFromMatrix]──> DESeq2 对象
       ↓
   预过滤（去掉总 count < 10 的基因）
       ↓
   DESeq() ──> 自动完成：
       ├── 估计 size factors（消除测序深度差异）
       ├── 估计 dispersion（负二项分布的方差参数）
       └── 拟合 GLM + Wald 检验
       ↓
   results() ──> 差异表达结果表
       └── baseMean, log2FC, pvalue, padj (FDR)
```

**实验设计公式 `design = ~ celltype + status` 的含义：**

这是**多因素线性模型**，同时估计两个因子的主效应：
- `celltype`：Luminal vs Basal 的差异（控制 status 的影响）
- `status`：发育状态的差异（控制 celltype 的影响）

如果你想分析 **交互作用**（比如"Luminal 在怀孕时的变化是否与 Basal 不同"），可以把设计改成：
```r
design = ~ celltype * status   # * 表示主效应 + 交互效应
```

---

### 11. Python 可视化

```bash
# 确保依赖已安装
pip install pandas matplotlib seaborn numpy

# 运行可视化脚本
python3 visualization.py
```

**输出图表说明：**

| 图表 | 用途 | 如何解读 |
|------|------|---------|
| **火山图** | 一眼看出上调/下调基因全貌 | 右上 = 显著上调；左上 = 显著下调；灰点 = 不显著 |
| **MA 图** | 检查低表达基因是否有系统性偏差 | 高表达基因（右侧）的 logFC 应该集中在 0 附近 |
| **PCA 图** | 看样本聚类是否符合实验设计 | 同组的样本应该聚类在一起 |
| **样本距离热图** | 检查生物学重复的一致性 | 重复的样本距离应该最近 |
| **Top 20 DEG 热图** | 看最显著差异基因在各样本中的表达模式 | 基因按行聚类，样本按列聚类 |
| **DEG 汇总柱状图** | 多个比较的差异基因数量对比 | 直观比较不同条件的响应强度 |

---

## 🔍 结果解读：预期发现

根据 Fu et al. 2015 的原文，你应该能在结果中看到：

### Luminal vs Basal
- **Luminal 高表达**：`Epcam`, `Krt8`, `Krt18`, `Krt19`（上皮细胞标志）
- **Basal 高表达**：`Krt5`, `Krt14`, `Acta2`, `Vim`（肌上皮/间质标志）

### 泌乳期的变化
- **泌乳相关基因**：`Csn2`（β-casein）、`Wap`（乳清酸性蛋白）、`Lalba`（α-乳白蛋白）在 **lactating 的 Luminal 细胞** 中显著上调
- **基底膜基因**：`Col1a1`, `Col1a2` 在 Basal 细胞中高表达

### 已知 marker 验证清单

| 基因 | 细胞类型/状态 | 你应在结果中看到 |
|------|-------------|----------------|
| Epcam | Luminal | 显著上调 (Luminal vs Basal) |
| Krt14 | Basal | 显著下调 (= Basal 高表达) |
| Csn2 | Lactating Luminal | lactating vs virgin 显著上调 |
| Gata3 | Luminal | 显著上调 |
| Trp63 | Basal | 显著下调 |

---

## 🐛 常见报错排查

### 1. STAR 建索引时 `Killed` / `Out of memory`

**原因**：WSL2 分配的内存不足。  
**解决**：
```bash
# 方案 A：降低 --genomeSAindexNbases（脚本已自动处理，内存<32GB时会自动降到11）
# 方案 B：改用 HISAT2
./run.sh --step 3 --aligner hisat2
```

### 2. `prefetch` 下载速度极慢

**原因**：NCBI 服务器在国外，国内访问慢。  
**解决**：
```bash
# 方法 1：换 aspera 高速下载（需额外安装 aspera-cli）
# 方法 2：用国内镜像（如 CNGBdb）
# 方法 3：用浏览器+IDM 等工具手动下载 SRA 文件，放到 raw_data/ 目录
```

### 3. `featureCounts` 报错 `failed to find the gene ID attribute`

**原因**：GTF 文件中 gene ID 的字段名不是 `gene_id`。  
**解决**：检查 GTF 文件的前几行，确认属性名：
```bash
zcat gencode.vM33.annotation.gtf.gz | head -5
# GENCODE 通常是 gene_id，但有些 GTF 用 GeneID 或 geneID
```

### 4. DESeq2 的 `sampleData` 和 `countData` 行/列名不匹配

**原因**：coldata 的行名和 counts 的列名不完全一致。  
**解决**：脚本里已用 `basename()` + `sub()` 自动提取 SRR 编号。如果仍报错，检查：
```r
colnames(counts)
rownames(coldata)
# 两者必须完全一致
```

### 5. Python 可视化报错 `KeyError: 'padj'`

**原因**：DESeq2 结果中有 NA 值（独立过滤去掉的基因）。  
**解决**：`visualization.py` 脚本已用 `dropna()` 处理，如果还报错，检查 CSV 文件是否正确生成。

---

## 📚 学习资源

| 资源 | 链接 | 说明 |
|------|------|------|
| edgeR 官方教程（本数据集） | [Bioconductor edgeR User's Guide](https://bioconductor.org/packages/release/bioc/vignettes/edgeR/inst/doc/edgeRUsersGuide.pdf) | 第 4.4 节就是 GSE60450 的完整分析 |
| DESeq2 官方 Vignette | [Analyzing RNA-seq data with DESeq2](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html) | DESeq2 的权威文档 |
| STAR 手册 | [STAR manual](https://github.com/alexdobin/STAR/blob/master/doc/STARmanual.pdf) | 所有参数详解 |
| featureCounts 指南 | [Subread User's Guide](http://bioinf.wehi.edu.au/subread-package/SubreadUsersGuide.pdf) | featureCounts 用法 |

---

## ✅ 检查清单（跑通后对照）

- [ ] WSL2 内存配置 ≥ 16GB（或已改用 HISAT2）
- [ ] `run.sh --step 1` 成功创建 conda 环境 `rnaseq`
- [ ] `run.sh --step 2` 下载了 mm39 FASTA + GTF
- [ ] `run.sh --step 3` 成功建索引（无 `Killed` 报错）
- [ ] `run.sh --step 4` 12 个 `.fastq.gz` 文件已下载
- [ ] `run.sh --step 5` MultiQC 报告 `qc/multiqc_report.html` 可正常打开
- [ ] `run.sh --step 6` 12 个 BAM 文件生成
- [ ] `run.sh --step 8` `counts/counts_matrix.txt` 有 12 列 + 基因名
- [ ] `deseq2_analysis.R` 成功运行，无报错
- [ ] `visualization.py` 生成了 `figures/` 下的所有图表
- [ ] 火山图上看到红色/蓝色的显著差异基因点
- [ ] 热图上 Luminal 和 Basal 样本明显分成两组
- [ ] Top DEG 中出现了 `Epcam`, `Krt14`, `Csn2` 等已知 marker

---

> 遇到任何报错，把 **完整的报错信息**（包括红色 ERROR 部分和前后的上下文）复制发给我，我会帮你定位问题。
