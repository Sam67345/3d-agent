#!/usr/bin/env bash
# ============================================================================
# run.sh - GSE60450 小鼠乳腺 RNA-seq 完整分析流程
# 适用：WSL2 + Ubuntu
# 作者环境：Python 较好、R 有基础、Linux 只会基本命令
#
# 运行前准备：
#   1. 确认 WSL2 内存 ≥ 16GB（STAR 建索引需要约 30GB，但可用 --genomeSAindexNbases 11 降到 16GB）
#      若内存 < 16GB，请改用 HISAT2（见脚本底部备选方案）
#   2. 确认硬盘剩余空间 ≥ 80GB（基因组 3GB + SRA 数据 ~12GB + 比对结果 ~30GB + 索引 ~30GB）
#   3. 给脚本执行权限：chmod +x run.sh
#   4. 建议用 tmux/screen 挂着跑，防止 SSH 断线
#
# 使用方法：
#   ./run.sh         # 运行全部步骤
#   ./run.sh --step 1   # 只运行第 1 步（环境准备）
#   ./run.sh --step 2   # 只运行第 2 步（参考基因组）
#   ...以此类推
#
# 流程总览：
#   Step 1: 安装 conda 和生物信息学工具
#   Step 2: 下载小鼠参考基因组和 GTF 注释
#   Step 3: STAR 建索引（或 HISAT2 建索引）
#   Step 4: 从 SRA 下载原始测序数据
#   Step 5: FastQC 质控
#   Step 6: STAR 比对（或 HISAT2 比对）
#   Step 7: samtools 排序和索引 BAM
#   Step 8: featureCounts 基因定量
# ============================================================================

set -euo pipefail    # 遇到错误立即退出；未定义变量报错；管道错误传递

# ---------------------------- 用户可修改的参数 ----------------------------
PROJECT_DIR="$(pwd)/GSE60450_analysis"   # 项目主目录
THREADS=$(nproc)                          # 自动检测 CPU 核心数
MEMORY_GB=16                              # 可用内存(GB)，根据你的电脑调整
# 若内存 < 16GB，请把下面改为 "hisat2"
ALIGNER="star"                            # "star" 或 "hisat2"
# -------------------------------------------------------------------------

# 固定路径
REF_DIR="${PROJECT_DIR}/reference"        # 参考基因组存放目录
RAW_DIR="${PROJECT_DIR}/raw_data"         # 原始 fastq 存放目录
QC_DIR="${PROJECT_DIR}/qc"                # FastQC 质控结果
ALIGN_DIR="${PROJECT_DIR}/alignment"      # 比对结果 BAM
COUNT_DIR="${PROJECT_DIR}/counts"         # 基因计数结果
LOG_DIR="${PROJECT_DIR}/logs"             # 日志目录

# SRA Run 编号列表（12 个样本）
SRR_LIST=(
    SRR1552444 SRR1552445 SRR1552446 SRR1552447
    SRR1552448 SRR1552449 SRR1552450 SRR1552451
    SRR1552452 SRR1552453 SRR1552454 SRR1552455
)

# 小鼠参考基因组 URL（GENCODE M33 / mm39）
GENCODE_VER="M33"
GENOME_URL="https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_${GENCODE_VER}/GRCm39.primary_assembly.genome.fa.gz"
GTF_URL="https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_${GENCODE_VER}/gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf.gz"

# ============================================================================
# 辅助函数：打印带时间戳的日志
# ============================================================================
log_info()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]  $*"; }
log_warn()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]  $*" >&2; }
log_error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; }

# ============================================================================
# Step 0: 创建目录结构
# ============================================================================
step_0_setup_dirs() {
    log_info "Step 0: 创建项目目录结构..."
    mkdir -p "${REF_DIR}" "${RAW_DIR}" "${QC_DIR}" "${ALIGN_DIR}" "${COUNT_DIR}" "${LOG_DIR}" "${PROJECT_DIR}/results"
    log_info "项目目录: ${PROJECT_DIR}"
    log_info "CPU 线程数: ${THREADS}"
    log_info "可用内存: ${MEMORY_GB}GB"
    log_info "比对器: ${ALIGNER}"
}

# ============================================================================
# Step 1: 安装 conda 和生物信息学工具
# ============================================================================
# conda 是 Python 的包/环境管理器，在生物信息学中用来安装预编译好的工具。
# 它比 apt 快，而且不会污染系统环境。
step_1_install_tools() {
    log_info "Step 1: 检查并安装 conda + 生物信息学工具..."

    # 检查 conda 是否已安装
    if ! command -v conda &>/dev/null; then
        log_info "conda 未找到，开始安装 Miniconda..."
        # 下载 Miniconda 安装脚本
        wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
            -O /tmp/miniconda.sh
        # -b: 静默安装(不交互); -p: 指定安装前缀
        bash /tmp/miniconda.sh -b -p "${HOME}/miniconda3"
        # 初始化 conda（把 conda 命令加入 shell 配置）
        "${HOME}/miniconda3/bin/conda" init bash
        log_warn "conda 已安装，请执行: source ~/.bashrc  然后重新运行脚本"
        exit 0
    fi

    log_info "conda 已安装，创建/激活 rnaseq 环境..."

    # conda create: 创建新环境; -n: 环境名; -y: 自动确认
    # bioconda 是生物信息学软件的 conda 频道
    # channel-priority strict: 确保包从正确的频道安装
    conda create -n rnaseq -c bioconda -c conda-forge \
        sra-tools fastqc multiqc star hisat2 samtools subread \
        pigz -y 2>&1 | tee "${LOG_DIR}/conda_install.log"

    # 验证安装（conda 位置自动探测，兼容 /mnt/e/miniconda3 等自定义路径）
    log_info "验证工具安装..."
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate rnaseq
    fastqc --version
    samtools --version | head -1
    STAR --version
    featureCounts -v 2>&1 | head -2

    log_info "Step 1 完成！所有工具已就绪。"
}

# ============================================================================
# Step 2: 下载小鼠参考基因组和 GTF 注释
# ============================================================================
# GENCODE 是目前最常用、注释最全面的哺乳动物基因组数据库。
# M33 对应 mm39 (GRCm39)，是小鼠最新的参考基因组版本。
step_2_download_reference() {
    log_info "Step 2: 下载小鼠参考基因组 (mm39/GRCm39) 和 GTF 注释..."
    cd "${REF_DIR}"

    # 基因组 FASTA（约 800MB 压缩后）
    # 始终用 wget -c：已下完的文件会直接跳过（416 响应），
    # 下一半的会从中断处续传，避免"文件存在就跳过"的半成品陷阱
    log_info "下载/续传基因组 FASTA..."
    wget -c --tries=0 --show-progress "${GENOME_URL}" \
        -O GRCm39.primary_assembly.genome.fa.gz

    # GTF 注释文件（约 40MB 压缩后）
    log_info "下载/续传 GTF 注释..."
    wget -c --tries=0 --show-progress "${GTF_URL}" \
        -O "gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf.gz"

    # STAR 无法直接读取 .gz（会报 "first character is not '>'" 错误，31= gzip 魔数），
    # 必须解压一份未压缩版本；保留 .gz 原件。
    # 用 .tmp + mv 保证中途被杀后重跑不会留下半成品 .fa
    if [[ ! -f "GRCm39.primary_assembly.genome.fa" ]]; then
        log_info "解压基因组 FASTA（738MB → 约 2.7GB，USB 盘上需几分钟）..."
        gunzip -c "GRCm39.primary_assembly.genome.fa.gz" > "GRCm39.primary_assembly.genome.fa.tmp" \
            && mv "GRCm39.primary_assembly.genome.fa.tmp" "GRCm39.primary_assembly.genome.fa"
        log_info "基因组 FASTA 解压完成。"
    fi
    if [[ ! -f "gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf" ]]; then
        log_info "解压 GTF 注释..."
        gunzip -c "gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf.gz" \
            > "gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf.tmp" \
            && mv "gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf.tmp" \
                   "gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf"
        log_info "GTF 解压完成。"
    fi

    log_info "Step 2 完成！参考文件已就绪。"
}

# ============================================================================
# Step 3: 建索引
# ============================================================================
# STAR 和 HISAT2 都需要先对参考基因组"建索引"——相当于给基因组做"目录"，
# 这样比对时就能快速定位 reads 来自哪个位置。
step_3_build_index() {
    log_info "Step 3: 为 ${ALIGNER} 建索引..."
    cd "${REF_DIR}"

    if [[ "${ALIGNER}" == "star" ]]; then
        log_info "使用 STAR 建索引（内存需求高，约 ${MEMORY_GB}GB）..."
        # --runThreadN: 并行线程数
        # --runMode genomeGenerate: 建索引模式（非比对模式）
        # --genomeDir: 索引输出目录
        # --genomeFastaFiles: 输入基因组 FASTA
        # --sjdbGTFfile: 输入 GTF（STAR 会据此构建剪接位点数据库）
        # --sjdbOverhang 49: read 长度 - 1（该数据集 read 长度约 50bp）
        # --genomeSAindexNbases: 控制索引大小。默认 14 需 ~30GB；
        #   若内存 < 32GB，设为 11 可降到 ~16GB
        SA_BASES=14
        if [[ ${MEMORY_GB} -lt 32 ]]; then
            SA_BASES=11
            log_warn "内存 < 32GB，自动调整 --genomeSAindexNbases=${SA_BASES}"
        fi

        STAR --runThreadN "${THREADS}" \
             --runMode genomeGenerate \
             --genomeDir "${REF_DIR}/STAR_index" \
             --genomeFastaFiles "${REF_DIR}/GRCm39.primary_assembly.genome.fa" \
             --sjdbGTFfile "${REF_DIR}/gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf" \
             --sjdbOverhang 49 \
             --genomeSAindexNbases "${SA_BASES}" \
             2>&1 | tee "${LOG_DIR}/star_index.log"

        log_info "STAR 索引已保存到 ${REF_DIR}/STAR_index"

    else
        log_info "使用 HISAT2 建索引（内存需求低，约 8GB）..."
        # hisat2-build: 建索引命令
        # -p: 线程数
        # 输出前缀: hisat2_index/GRCm39
        mkdir -p "${REF_DIR}/hisat2_index"
        hisat2-build -p "${THREADS}" \
            "${REF_DIR}/GRCm39.primary_assembly.genome.fa.gz" \
            "${REF_DIR}/hisat2_index/GRCm39" \
            2>&1 | tee "${LOG_DIR}/hisat2_index.log"

        log_info "HISAT2 索引已保存到 ${REF_DIR}/hisat2_index"
    fi

    log_info "Step 3 完成！索引构建完毕。"
}

# ============================================================================
# Step 4: 从 SRA 下载原始测序数据
# ============================================================================
# prefetch: NCBI sra-toolkit 的工具，从 SRA 数据库下载原始数据。
# 它会下载 .sra 文件，之后用 fasterq-dump 转成 .fastq。
# -X 30g: 限制每个下载任务最大使用 30GB 缓存空间
# -O: 输出目录
step_4_download_sra() {
    log_info "Step 4: 从 SRA 下载 12 个样本的原始数据..."
    mkdir -p "${RAW_DIR}"
    cd "${RAW_DIR}"

    # 配置 sra-toolkit（设置下载缓存位置）
    # vdb-config -i 是交互式配置，这里用非交互式设置
    mkdir -p "${PROJECT_DIR}/sra_cache"
    export VDB_CONFIG="${PROJECT_DIR}/sra_cache"

    for srr in "${SRR_LIST[@]}"; do
        if [[ -f "${srr}_1.fastq.gz" ]] || [[ -f "${srr}.fastq.gz" ]]; then
            log_info "${srr} 已存在，跳过。"
            continue
        fi

        log_info "下载 ${srr} ..."
        # prefetch: 下载 .sra 文件到本地缓存
        prefetch "${srr}" --max-size 50G -O "${RAW_DIR}" \
            2>&1 | tee "${LOG_DIR}/${srr}_prefetch.log"

        # fasterq-dump: 把 .sra 转成 .fastq
        # --split-files: 若是双端测序，拆成 _1.fastq 和 _2.fastq
        # --threads: 并行线程
        # --progress: 显示进度
        # --outdir: 输出目录
        fasterq-dump "${srr}" \
            --split-files \
            --threads "${THREADS}" \
            --progress \
            --outdir "${RAW_DIR}" \
            2>&1 | tee "${LOG_DIR}/${srr}_fasterq.log"

        # pigz: 并行 gzip 压缩（比 gzip 快很多，因为有 -p 多线程）
        # -p: 线程数; -f: 强制覆盖
        if [[ -f "${RAW_DIR}/${srr}_1.fastq" ]]; then
            # 双端数据
            pigz -p "${THREADS}" -f "${RAW_DIR}/${srr}"_*.fastq
        elif [[ -f "${RAW_DIR}/${srr}.fastq" ]]; then
            # 单端数据
            pigz -p "${THREADS}" -f "${RAW_DIR}/${srr}.fastq"
        fi

        # 清理 .sra 缓存文件释放空间
        rm -f "${RAW_DIR}/${srr}/${srr}.sra" 2>/dev/null || true
        rmdir "${RAW_DIR}/${srr}" 2>/dev/null || true
    done

    log_info "Step 4 完成！所有 fastq 文件已下载到 ${RAW_DIR}"
    ls -lh "${RAW_DIR}"/*.fastq.gz | tee "${LOG_DIR}/raw_data_list.txt"
}

# ============================================================================
# Step 5: FastQC 质控
# ============================================================================
# FastQC 是 RNA-seq 质控的标准工具，它会检查：
#   - 碱基质量分布（Per base sequence quality）
#   - GC 含量（Per sequence GC content）
#   - 接头污染（Adapter Content）
#   - 重复 reads 比例（Sequence Duplication Levels）
# 质量好的数据，大部分碱基的 Phred 质量应该在 Q30 以上（绿色区域）。
step_5_fastqc() {
    log_info "Step 5: 运行 FastQC 质控..."
    mkdir -p "${QC_DIR}"

    # -t: 线程数; -o: 输出目录
    fastqc -t "${THREADS}" \
           -o "${QC_DIR}" \
           "${RAW_DIR}"/*.fastq.gz \
           2>&1 | tee "${LOG_DIR}/fastqc.log"

    log_info "FastQC 完成。单个报告在 ${QC_DIR}/*.html"

    # MultiQC 把所有 FastQC 报告汇总成一个网页
    log_info "运行 MultiQC 汇总质控报告..."
    # -o: 输出目录; -n: 报告文件名; 输入目录
    multiqc "${QC_DIR}" \
        -o "${QC_DIR}" \
        -n "multiqc_report" \
        2>&1 | tee "${LOG_DIR}/multiqc.log"

    log_info "Step 5 完成！MultiQC 汇总报告: ${QC_DIR}/multiqc_report.html"
}

# ============================================================================
# Step 6: STAR / HISAT2 比对
# ============================================================================
# 比对（alignment）是把测序 reads 映射回参考基因组的过程。
# STAR 是目前 RNA-seq 比对的金标准，对剪接位点（splice junction）敏感。
# 输出是 SAM/BAM 格式，记录每个 read 来自哪个染色体、哪个位置。
step_6_alignment() {
    log_info "Step 6: 运行 ${ALIGNER} 比对..."
    mkdir -p "${ALIGN_DIR}"

    for srr in "${SRR_LIST[@]}"; do
        log_info "比对 ${srr} ..."

        # 判断是单端还是双端
        if [[ -f "${RAW_DIR}/${srr}_1.fastq.gz" ]]; then
            READS="${RAW_DIR}/${srr}_1.fastq.gz ${RAW_DIR}/${srr}_2.fastq.gz"
            IS_PAIRED=true
        else
            READS="${RAW_DIR}/${srr}.fastq.gz"
            IS_PAIRED=false
        fi

        if [[ "${ALIGNER}" == "star" ]]; then
            # --genomeDir: 索引目录
            # --readFilesIn: 输入 fastq（单端一个文件，双端两个文件）
            # --readFilesCommand zcat: 自动解压 .gz 文件
            # --outFileNamePrefix: 输出前缀
            # --outSAMtype BAM SortedByCoordinate: 直接输出按坐标排序的 BAM
            # --outSAMunmapped Within: 把没比对上的 reads 也保留在 BAM 里
            # --outFilterType BySJout: 基于剪接位点过滤
            # --outFilterMultimapNmax 20: 允许一个 read 最多比对到 20 个位置
            # --alignSJoverhangMin 8: 剪接位点两侧最少 8bp 覆盖
            # --alignSJDBoverhangMin 1: 已知剪接位点最少 1bp 覆盖
            # --outFilterMismatchNmax 999: 错配数阈值（配合 mismatchPerRead 用）
            # --outFilterMismatchNoverReadLmax 0.04: 错配率 < 4%
            # --alignIntronMin 20: 内含子最小长度 20bp
            # --alignIntronMax 1000000: 内含子最大长度 1Mb
            # --alignMatesGapMax 1000000: pair 间最大距离 1Mb
            # --quantMode GeneCounts: 同时输出 read-per-gene 计数（类似 htseq-count）
            STAR --genomeDir "${REF_DIR}/STAR_index" \
                 --readFilesIn ${READS} \
                 --readFilesCommand zcat \
                 --runThreadN "${THREADS}" \
                 --outFileNamePrefix "${ALIGN_DIR}/${srr}_" \
                 --outSAMtype BAM SortedByCoordinate \
                 --outSAMunmapped Within \
                 --outFilterType BySJout \
                 --outFilterMultimapNmax 20 \
                 --alignSJoverhangMin 8 \
                 --alignSJDBoverhangMin 1 \
                 --outFilterMismatchNmax 999 \
                 --outFilterMismatchNoverReadLmax 0.04 \
                 --alignIntronMin 20 \
                 --alignIntronMax 1000000 \
                 --alignMatesGapMax 1000000 \
                 --quantMode GeneCounts \
                 2>&1 | tee "${LOG_DIR}/${srr}_star.log"
        else
            # HISAT2 比对
            # -p: 线程数
            # -x: 索引前缀
            # -1/-2: 双端输入; -U: 单端输入
            # --dta: 为 StringTie 等工具优化（输出 XS 标签）
            # --rna-strandness RF: 链特异性信息（Illumina TruSeq 通常是 RF）
            # -S: 输出 SAM（后续用 samtools 转 BAM）
            if [[ "${IS_PAIRED}" == true ]]; then
                hisat2 -p "${THREADS}" \
                    -x "${REF_DIR}/hisat2_index/GRCm39" \
                    -1 "${RAW_DIR}/${srr}_1.fastq.gz" \
                    -2 "${RAW_DIR}/${srr}_2.fastq.gz" \
                    --dta \
                    --rna-strandness RF \
                    -S "${ALIGN_DIR}/${srr}.sam" \
                    2>&1 | tee "${LOG_DIR}/${srr}_hisat2.log"
            else
                hisat2 -p "${THREADS}" \
                    -x "${REF_DIR}/hisat2_index/GRCm39" \
                    -U "${RAW_DIR}/${srr}.fastq.gz" \
                    --dta \
                    --rna-strandness R \
                    -S "${ALIGN_DIR}/${srr}.sam" \
                    2>&1 | tee "${LOG_DIR}/${srr}_hisat2.log"
            fi

            # HISAT2 输出 SAM，需要转成排序后的 BAM
            samtools sort -@ "${THREADS}" \
                -o "${ALIGN_DIR}/${srr}.sorted.bam" \
                "${ALIGN_DIR}/${srr}.sam"
            rm -f "${ALIGN_DIR}/${srr}.sam"
        fi
    done

    log_info "Step 6 完成！所有样本比对完毕。"
}

# ============================================================================
# Step 7: samtools 排序和索引 BAM
# ============================================================================
# STAR 已经输出排序后的 BAM，这里只对 HISAT2 做补充。
# 同时给所有 BAM 建索引（.bai），这是 IGV 等可视化工具的必需文件。
step_7_samtools_index() {
    log_info "Step 7: 为 BAM 文件建索引..."

    for srr in "${SRR_LIST[@]}"; do
        # STAR 输出: ${srr}_Aligned.sortedByCoord.out.bam
        # HISAT2 输出: ${srr}.sorted.bam
        if [[ "${ALIGNER}" == "star" ]]; then
            BAM="${ALIGN_DIR}/${srr}_Aligned.sortedByCoord.out.bam"
        else
            BAM="${ALIGN_DIR}/${srr}.sorted.bam"
        fi

        if [[ -f "${BAM}" ]] && [[ ! -f "${BAM}.bai" ]]; then
            # index: 建 .bai 索引; -@: 线程数
            samtools index -@ "${THREADS}" "${BAM}"
            log_info "已索引 ${BAM}"
        fi
    done

    log_info "Step 7 完成！所有 BAM 已索引。"
}

# ============================================================================
# Step 8: featureCounts 基因定量
# ============================================================================
# featureCounts 是 subread 软件包中的基因定量工具。
# 它读取 BAM 文件，统计每个基因上有多少 reads 落上去，输出 count matrix。
# 这个 count matrix 就是 DESeq2 / edgeR 的输入。
step_8_featurecounts() {
    log_info "Step 8: featureCounts 基因定量..."
    mkdir -p "${COUNT_DIR}"

    # 收集所有 BAM 文件路径
    BAM_FILES=""
    for srr in "${SRR_LIST[@]}"; do
        if [[ "${ALIGNER}" == "star" ]]; then
            BAM="${ALIGN_DIR}/${srr}_Aligned.sortedByCoord.out.bam"
        else
            BAM="${ALIGN_DIR}/${srr}.sorted.bam"
        fi
        BAM_FILES="${BAM_FILES} ${BAM}"
    done

    # -T: 线程数
    # -a: GTF 注释文件
    # -o: 输出文件
    # -g gene_id: 按 gene_id 汇总（GTF 中的 gene ID，如 ENSMUSG...）
    # -t exon: 只统计落在 exon 上的 reads
    # --largestOverlap: 如果一个 read 跨多个基因，分配给重叠最大的那个
    # -p: 双端模式（pair-end）；若是单端数据请删除此参数
    # --countReadPairs: 把一对 reads 算作一次计数（而不是两次）
    featureCounts -T "${THREADS}" \
        -a "${REF_DIR}/gencode.v${GENCODE_VER}.primary_assembly.annotation.gtf.gz" \
        -o "${COUNT_DIR}/featurecounts.txt" \
        -g gene_id \
        -t exon \
        --largestOverlap \
        -p \
        --countReadPairs \
        ${BAM_FILES} \
        2>&1 | tee "${LOG_DIR}/featurecounts.log"

    # featureCounts 输出格式：前两列是基因信息和长度，后面每列是一个样本。
    # 为了后续 DESeq2 方便，提取纯计数矩阵（去掉 Description 和 Length 列）。
    # 第 1 列是 Geneid，第 7 列开始是各样本计数。
    log_info "提取纯计数矩阵..."
    cut -f1,7- "${COUNT_DIR}/featurecounts.txt" | \
        tail -n +3 > "${COUNT_DIR}/counts_matrix.txt"

    log_info "Step 8 完成！计数矩阵: ${COUNT_DIR}/counts_matrix.txt"
}

# ============================================================================
# 主函数：解析命令行参数并分发到各步骤
# ============================================================================
main() {
    local step="all"

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --step)
                step="$2"
                shift 2
                ;;
            --aligner)
                ALIGNER="$2"
                shift 2
                ;;
            --memory)
                MEMORY_GB="$2"
                shift 2
                ;;
            -h|--help)
                echo "Usage: $0 [--step N] [--aligner star|hisat2] [--memory GB]"
                echo "  --step N     : 只运行第 N 步 (0-8)"
                echo "  --aligner    : 选择比对器 (star 或 hisat2)"
                echo "  --memory     : 可用内存 (GB)，默认 16"
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                exit 1
                ;;
        esac
    done

    # 确保目录存在（用户可能跳过 step 0 直接跑后续步骤，tee 写日志需要 logs/ 目录）
    mkdir -p "${PROJECT_DIR}" "${LOG_DIR}"

    # 确保 conda 环境可用（自动探测 conda 安装位置，支持移动硬盘等自定义路径）
    CONDA_BASE="$(conda info --base 2>/dev/null || echo "${HOME}/miniconda3")"
    if [[ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]]; then
        source "${CONDA_BASE}/etc/profile.d/conda.sh"
        conda activate rnaseq 2>/dev/null || log_warn "rnaseq 环境未激活，请确保工具已安装"
    fi

    # 执行步骤
    case "${step}" in
        0) step_0_setup_dirs ;;
        1) step_1_install_tools ;;
        2) step_2_download_reference ;;
        3) step_3_build_index ;;
        4) step_4_download_sra ;;
        5) step_5_fastqc ;;
        6) step_6_alignment ;;
        7) step_7_samtools_index ;;
        8) step_8_featurecounts ;;
        all)
            step_0_setup_dirs
            step_1_install_tools
            step_2_download_reference
            step_3_build_index
            step_4_download_sra
            step_5_fastqc
            step_6_alignment
            step_7_samtools_index
            step_8_featurecounts
            log_info "=========================================="
            log_info "🎉 全部步骤完成！W2 原始数据处理结束。"
            log_info "计数矩阵位置: ${COUNT_DIR}/counts_matrix.txt"
            log_info "接下来请运行 W3 的 DESeq2 分析脚本。"
            log_info "=========================================="
            ;;
        *)
            log_error "无效的步骤号: ${step}，请用 0-8 或 all"
            exit 1
            ;;
    esac
}

main "$@"
