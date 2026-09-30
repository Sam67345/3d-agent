import fs from "node:fs";
import path from "node:path";
import {
  AlignmentType,
  Document,
  Footer,
  Header,
  HeadingLevel,
  ImageRun,
  ImportedXmlComponent,
  LevelFormat,
  Packer,
  PageBreak,
  PageNumber,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
  convertInchesToTwip,
} from "docx";

const outputPath = process.argv[2];
if (!outputPath) throw new Error("Usage: node create_report.js /abs/path/output.docx");

const outputDir = path.dirname(outputPath);
const assetDir = path.join(outputDir, "report_assets");

// ---------- 视觉体系：学术深蓝灰，单色系三档 ----------
const palette = {
  dark: "1F3864",
  primary: "2E5395",
  light: "8496B0",
  border: "B4C6E7",
  fill: "EDF1F8",
  codebg: "F4F5F7",
};

const bodyFont = { ascii: "Times New Roman", hAnsi: "Times New Roman", cs: "Times New Roman", eastAsia: "SimSun" };
const headFont = { ascii: "Times New Roman", hAnsi: "Times New Roman", cs: "Times New Roman", eastAsia: "SimHei" };
const codeFont = { ascii: "Consolas", hAnsi: "Consolas", cs: "Consolas", eastAsia: "SimSun" };

const run = (text, options = {}) => new TextRun({ text, font: bodyFont, size: 24, ...options });
const hrun = (text, options = {}) => new TextRun({ text, font: headFont, ...options });
const para = (children, options = {}) => new Paragraph({
  spacing: { after: 160, line: 300 },
  ...options,
  children: Array.isArray(children) ? children : [children],
});
const body = (text, options = {}) => para(run(text), { indent: { firstLine: convertInchesToTwip(0.33) }, ...options });

const h1 = (text) => para(hrun(text, { bold: true, size: 30, color: palette.dark }), {
  heading: HeadingLevel.HEADING_1, spacing: { before: 320, after: 160 },
});
const h2 = (text) => para(hrun(text, { bold: true, size: 26, color: palette.primary }), {
  heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 },
});

const bullet = (text) => para(run(text), {
  numbering: { reference: "bullet", level: 0 },
  spacing: { after: 100, line: 300 },
});

const codeParas = (lines) => lines.map((ln) => para(run(ln, { font: codeFont, size: 18, color: "333333" }), {
  shading: { type: ShadingType.CLEAR, fill: palette.codebg, color: "auto" },
  spacing: { after: 0, line: 260 },
  indent: { left: 240 },
}));

// ---------- 表格 ----------
const makeTable = (widths, header, rows) => {
  const mkCell = (text, isHeader, w) => new TableCell({
    children: [para(run(text, { bold: isHeader, size: 21 }), { spacing: { after: 40, line: 280 } })],
    margins: { top: 80, bottom: 80, left: 100, right: 100 },
    width: { size: w, type: WidthType.DXA },
    shading: isHeader ? { type: ShadingType.CLEAR, fill: palette.fill } : undefined,
  });
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({ children: header.map((t, i) => mkCell(t, true, widths[i])) }),
      ...rows.map((r) => new TableRow({ children: r.map((t, i) => mkCell(t, false, widths[i])) })),
    ],
  });
};

// ---------- 图片（按 PNG 原始宽高比自动配高） ----------
const pngSize = (file) => {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
};
const figureCount = { n: 0 };
const img = (file, caption, widthPx = 520) => {
  const fp = path.join(assetDir, file);
  const { w, h } = pngSize(fp);
  figureCount.n += 1;
  const label = "图" + figureCount.n;
  return [
    para(new ImageRun({
      type: "png",
      data: fs.readFileSync(fp),
      transformation: { width: widthPx, height: Math.round((widthPx * h) / w) },
    }), { alignment: AlignmentType.CENTER, spacing: { before: 160, after: 60 } }),
    para(run(label + "  " + caption, { size: 20, color: palette.light }), {
      alignment: AlignmentType.CENTER, spacing: { after: 200 },
    }),
  ];
};

// ---------- 目录 ----------
const xmlEscape = (v) => String(v)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

const toc = (entries) => {
  const cached = entries.map(({ title, level, page }) => {
    const indent = Math.max(0, level - 1) * 360;
    return '<w:p><w:pPr><w:pStyle w:val="TOC' + level + '"/>'
      + '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9000"/></w:tabs>'
      + '<w:ind w:left="' + indent + '"/></w:pPr>'
      + '<w:r><w:t>' + xmlEscape(title) + '</w:t></w:r><w:r><w:tab/></w:r>'
      + '<w:r><w:t>' + xmlEscape(page) + '</w:t></w:r></w:p>';
  }).join("");
  return ImportedXmlComponent.fromXmlString(
    '<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    + '<w:sdtPr><w:alias w:val="目录"/></w:sdtPr><w:sdtContent>'
    + '<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/>'
    + '<w:instrText xml:space="preserve"> TOC \\o &quot;1-3&quot; \\h \\z \\u </w:instrText>'
    + '<w:fldChar w:fldCharType="separate"/></w:r></w:p>'
    + cached
    + '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    + '</w:sdtContent></w:sdt>'
  ).root[0];
};

const pageBreak = () => new Paragraph({ children: [new PageBreak()], spacing: { after: 0 } });

// ================= 目录条目（与正文标题 1:1 对应，页码为估计值，Word 打开后可右键更新域） =================
const TOC_ENTRIES = [
  { title: "一、项目概述", level: 1, page: "3" },
  { title: "1.1 数据集与实验设计", level: 2, page: "3" },
  { title: "1.2 分析目标", level: 2, page: "4" },
  { title: "二、计算环境", level: 1, page: "5" },
  { title: "2.1 集群硬件", level: 2, page: "5" },
  { title: "2.2 软件环境", level: 2, page: "5" },
  { title: "2.3 参考基因组与注释", level: 2, page: "6" },
  { title: "三、分析流程与方法", level: 1, page: "7" },
  { title: "3.1 原始数据下载", level: 2, page: "7" },
  { title: "3.2 STAR 索引构建", level: 2, page: "7" },
  { title: "3.3 SRA 转 FASTQ", level: 2, page: "8" },
  { title: "3.4 序列比对（作业数组）", level: 2, page: "8" },
  { title: "3.5 基因表达定量", level: 2, page: "9" },
  { title: "3.6 差异表达分析", level: 2, page: "10" },
  { title: "3.7 质量控制", level: 2, page: "10" },
  { title: "四、结果", level: 1, page: "11" },
  { title: "4.1 比对质量", level: 2, page: "11" },
  { title: "4.2 样本聚类与 PCA", level: 2, page: "11" },
  { title: "4.3 差异表达统计与火山图", level: 2, page: "12" },
  { title: "4.4 Top 差异基因", level: 2, page: "14" },
  { title: "五、讨论与局限", level: 1, page: "15" },
  { title: "六、问题排查记录", level: 1, page: "16" },
  { title: "七、技能收获总结", level: 1, page: "17" },
  { title: "附录 A  项目目录结构", level: 1, page: "18" },
  { title: "附录 B  脚本清单", level: 1, page: "18" },
];

// ================= 正文内容块 =================
const blocks = [
  // ---------- 一、项目概述 ----------
  { k: "h1", t: "一、项目概述" },
  { k: "h2", t: "1.1 数据集与实验设计" },
  { k: "p", t: "本分析复现 Fu 等人（2015）发表的小鼠乳腺上皮细胞 RNA-seq 数据集（GEO 编号 GSE60450，SRA 编号 SRP045534）。该研究比较小鼠乳腺中两类主要上皮细胞——基底细胞（Basal）与管腔细胞（Luminal）——在三个生理时期（未孕 virgin、孕期 pregnant、泌乳期 lactating）的转录组差异，为理解乳腺发育与泌乳的分子机制提供基础数据。" },
  { k: "p", t: "实验设计为 2 种细胞类型 × 3 个时期 × 2 个生物学重复 = 12 个样本，均为单端 50 bp 测序。样本清单如下表（样本命名与分组沿用原文）。" },
  { k: "table", widths: [1100, 1700, 1400, 1500, 1800],
    header: ["样本名", "SRA 编号", "细胞类型", "时期", "分组"],
    rows: [
      ["LV1", "SRR1552444", "Luminal", "virgin", "Luminal_virgin"],
      ["LV2", "SRR1552445", "Luminal", "virgin", "Luminal_virgin"],
      ["LP1", "SRR1552446", "Luminal", "pregnant", "Luminal_pregnant"],
      ["LP2", "SRR1552447", "Luminal", "pregnant", "Luminal_pregnant"],
      ["LL1", "SRR1552448", "Luminal", "lactating", "Luminal_lactating"],
      ["LL2", "SRR1552449", "Luminal", "lactating", "Luminal_lactating"],
      ["BV1", "SRR1552450", "Basal", "virgin", "Basal_virgin"],
      ["BV2", "SRR1552451", "Basal", "virgin", "Basal_virgin"],
      ["BP1", "SRR1552452", "Basal", "pregnant", "Basal_pregnant"],
      ["BP2", "SRR1552453", "Basal", "pregnant", "Basal_pregnant"],
      ["BL1", "SRR1552454", "Basal", "lactating", "Basal_lactating"],
      ["BL2", "SRR1552455", "Basal", "lactating", "Basal_lactating"],
    ] },
  { k: "h2", t: "1.2 分析目标" },
  { k: "b", t: "搭建一套在实验室集群上可复现的 RNA-seq 标准流程（下载 → 比对 → 定量 → 差异分析 → 可视化）；" },
  { k: "b", t: "对原始测序数据进行质量控制评估（测序质量、比对率、计数分配）；" },
  { k: "b", t: "鉴定不同细胞类型间、以及不同发育时期间的差异表达基因（DEGs）；" },
  { k: "b", t: "通过 PCA、聚类热图、火山图等展示样本关系与差异表达格局，并与原文结论对照。" },

  // ---------- 二、计算环境 ----------
  { k: "h1", t: "二、计算环境" },
  { k: "h2", t: "2.1 集群硬件" },
  { k: "p", t: "分析在实验室集群上完成。集群由登录/存储节点 ds01 与计算节点 cn1 组成，任务通过 PBS 调度系统提交。登录节点仅用于轻量操作（下载、编辑、提交），所有计算密集型步骤（建索引、比对、定量）均以 PBS 作业提交至 cn1 执行。" },
  { k: "table", widths: [1900, 2600, 2500, 2000],
    header: ["节点", "角色", "配置", "用途"],
    rows: [
      ["ds01", "登录/存储节点", "20 核 / 62 GB 内存", "登录、下载、脚本编辑、任务提交"],
      ["cn1", "计算节点", "112 核 / 502 GB 内存", "PBS 作业实际执行"],
      ["/data/R01", "共享存储", "73 TB（可用 12 TB）", "家目录与全部项目数据"],
    ] },
  { k: "h2", t: "2.2 软件环境" },
  { k: "p", t: "软件通过 Miniconda 安装管理，按用途分为两个隔离环境：rnaseq（原始数据处理工具链）与 r_env（统计分析与绘图）。核心软件版本如下表。" },
  { k: "table", widths: [2200, 1700, 5100],
    header: ["软件", "版本", "用途"],
    rows: [
      ["SRA Toolkit", "3.0.5", "SRA 数据下载（prefetch）与格式转换（fasterq-dump）"],
      ["FastQC", "0.12.1", "原始测序数据质量评估"],
      ["MultiQC", "1.15", "汇总多个样本的质控报告"],
      ["STAR", "2.7.10b", "RNA-seq 剪接感知比对 / 基因组索引构建"],
      ["SAMtools", "1.18", "BAM 文件查看与统计"],
      ["featureCounts", "2.0.6", "基因水平 read 计数（subread 包）"],
      ["pigz", "2.8", "并行 gzip 压缩"],
      ["R", "4.5.3", "统计计算环境（r_env）"],
      ["DESeq2", "1.50.2", "负二项模型差异表达分析（Bioconductor）"],
      ["Python", "3.12", "可视化脚本运行环境（pandas / matplotlib / seaborn）"],
    ] },
  { k: "h2", t: "2.3 参考基因组与注释" },
  { k: "p", t: "参考基因组采用小鼠 GRCm39（primary assembly），基因注释采用 GENCODE 第 M33 版，均从 EBI 官方 FTP 下载。基因组 FASTA 解压后约 2.6 GB，注释 GTF 约 868 MB；二者经完整性校验（字节数与官方一致）后用于建索引。" },
];

blocks.push(
  // ---------- 三、分析流程与方法 ----------
  { k: "h1", t: "三、分析流程与方法" },
  { k: "p", t: "整体流程为：原始数据下载 → 参考基因组与索引 → SRA 转 FASTQ → STAR 比对 → featureCounts 定量 → 质控汇总 → DESeq2 差异分析 → 可视化。所有计算步骤均封装为 PBS 作业脚本，通过 qsub 提交至 cn1 队列执行，作业日志完整留档，保证可复现。" },

  { k: "h2", t: "3.1 原始数据下载" },
  { k: "p", t: "使用 SRA Toolkit 的 prefetch 从 NCBI 下载 12 个样本的 SRA 原始数据（共约 21.7 GB），脚本以 nohup 挂至后台过夜执行，内置断点续传与失败重试。校园网带宽下全程约 1.5 小时。" },
  { k: "code", lines: [
    "prefetch -O raw_data SRR1552444   # 下载单个样本（其余 11 个同法）",
    "ls raw_data/ | wc -l              # 验收：12 个样本目录",
    "du -sh raw_data/                  # 验收：共约 22 G",
  ] },

  { k: "h2", t: "3.2 STAR 索引构建" },
  { k: "p", t: "比对前需先构建基因组索引。STAR 的 genomeGenerate 模式读取基因组 FASTA 与 GTF 注释，将序列与剪接位点信息编译为二进制索引（约 25 GB）。该步骤内存需求约 30 GB，在 cn1 上以 16 线程运行约 25 分钟完成。核心参数如下表。" },
  { k: "table", widths: [2600, 1700, 4700],
    header: ["参数", "取值", "含义"],
    rows: [
      ["--runMode genomeGenerate", "—", "指定为索引构建模式（默认模式为比对）"],
      ["--genomeDir", "STAR_index", "索引输出目录"],
      ["--genomeFastaFiles", "GRCm39.fa", "参考基因组序列"],
      ["--sjdbGTFfile", "gencode.vM33.gtf", "注释文件，用于预构建剪接位点数据库"],
      ["--sjdbOverhang", "49", "read 长度减 1（本数据 read 长 50 bp）"],
      ["--genomeSAindexNbases", "14", "后缀数组索引深度，哺乳动物基因组标准值"],
      ["--runThreadN", "16", "并行线程数（与 PBS 申请核数一致）"],
    ] },

  { k: "h2", t: "3.3 SRA 转 FASTQ" },
  { k: "p", t: "fasterq-dump 将 .sra 转换为标准 FASTQ 格式，单样本约 1.5 分钟。由于原始 FASTQ 未压缩（12 个样本合计约 115 GB），转换后用 pigz 压缩至约 30 GB；压缩格式与 gzip 完全兼容，下游工具可直接读取。测序量验收：每样本 2500–3100 万条 reads。" },
  { k: "code", lines: [
    "fasterq-dump -O fastq/ -e 4 --split-files raw_data/SRR1552444",
    "pigz -p 8 fastq/*.fastq        # 并行压缩，115 G -> 约 30 G",
  ] },

  { k: "h2", t: "3.4 序列比对（作业数组）" },
  { k: "p", t: "采用 STAR 将 reads 比对至 GRCm39。STAR 专为剪接比对设计，能正确识别跨外显子接缝的 reads。12 个样本以 PBS 作业数组方式并行处理：一次提交生成 12 个子任务，每个子任务通过环境变量 PBS_ARRAYID 领取自己的样本，独占 8 核 / 45 GB 内存，单样本约 15–25 分钟；全部 12 个样本在 1 小时内完成。比对直接输出按坐标排序的 BAM 文件。" },
  { k: "code", lines: [
    "qsub -t 0-11 align.pbs    # 提交 12 个子任务",
    "",
    "# align.pbs 核心命令：",
    "STAR --genomeDir reference/STAR_index \\",
    "     --readFilesIn fastq/SRR1552444.fastq.gz \\",
    "     --readFilesCommand gunzip -c \\",
    "     --runThreadN 8 \\",
    "     --outSAMtype BAM SortedByCoordinate \\",
    "     --outFileNamePrefix alignment/SRR1552444/.",
  ] },
  { k: "p", t: "其中 --readFilesCommand gunzip -c 让 STAR 直接读取压缩的 FASTQ，解压不产生中间文件。验收指标为各样本 Log.final.out 中的唯一比对率（Uniquely mapped reads %），详见 4.1 节。" },

  { k: "h2", t: "3.5 基因表达定量" },
  { k: "p", t: "使用 featureCounts 一次性读取全部 12 个 BAM，按 GENCODE M33 注释统计每个基因的 read 数，直接产出列对齐的计数矩阵 count_matrix.txt（行 = 基因，列 = 样本）。未显式指定的关键默认行为：仅统计唯一比对的 reads、按外显子区计数、以 gene_id 为行名、不分链（-s 0，经 STAR 链特异性检验确认为非链特异性文库）。" },

  { k: "h2", t: "3.6 差异表达分析" },
  { k: "p", t: "差异表达分析使用 DESeq2（R 4.5.3）。实验设计公式为 ~ celltype + status，即在控制另一因子的同时分别估计细胞类型与发育时期的主效应。低表达基因（所有样本合计 count < 10）预先过滤；对计数做 vst 方差稳定化变换后用于 PCA 与聚类；统计检验采用 Wald 检验，p 值经 BH 法校正。显著差异基因筛选标准：校正后 p 值（padj）< 0.05 且 |log2 倍数变化| > 1。共进行三组对比：" },
  { k: "table", widths: [2600, 2000, 4400],
    header: ["对比", "因子", "生物学含义"],
    rows: [
      ["Luminal vs Basal", "celltype", "两种上皮细胞类型的转录组差异"],
      ["pregnant vs virgin", "status", "孕期相对未孕的表达变化"],
      ["lactating vs virgin", "status", "泌乳期相对未孕的表达变化"],
    ] },

  { k: "h2", t: "3.7 质量控制" },
  { k: "p", t: "FastQC 对 12 个 FASTQ 逐一生成质控报告（24 个文件），MultiQC 汇总为单一 HTML（qc/GSE60450-QC_multiqc_report.html）。测序质量随读长衰减的模式、接头污染水平均在正常范围；详细结果见 4.1 节与附录 A 目录中的 multiqc 报告文件。" },
);

blocks.push(
  // ---------- 四、结果 ----------
  { k: "h1", t: "四、结果" },
  { k: "h2", t: "4.1 比对质量" },
  { k: "p", t: "12 个样本的 STAR 唯一比对率介于 87.55%–92.74% 之间（健康区间 75–90% 以上），表明绝大部分 reads 能唯一地定位到参考基因组，样品质量与比对参数均可靠。" },
  { k: "table", widths: [1500, 2300, 2400, 2800],
    header: ["样本", "分组", "唯一比对率", "总 reads（万）"],
    rows: [
      ["SRR1552444", "Luminal_virgin", "87.55%", "2792"],
      ["SRR1552445", "Luminal_virgin", "87.48%", "2973"],
      ["SRR1552446", "Luminal_pregnant", "88.23%", "2988"],
      ["SRR1552447", "Luminal_pregnant", "89.04%", "2925"],
      ["SRR1552448", "Luminal_lactating", "92.51%", "3143"],
      ["SRR1552449", "Luminal_lactating", "92.74%", "3128"],
      ["SRR1552450", "Basal_virgin", "89.75%", "3011"],
      ["SRR1552451", "Basal_virgin", "89.18%", "2832"],
      ["SRR1552452", "Basal_pregnant", "89.39%", "3169"],
      ["SRR1552453", "Basal_pregnant", "89.21%", "2961"],
      ["SRR1552454", "Basal_lactating", "90.41%", "2723"],
      ["SRR1552455", "Basal_lactating", "90.51%", "2543"],
    ] },
  { k: "note", t: "注：featureCounts 统计中 Assigned 比例（约占总 reads 的 65–75%）以全部 reads 为分母；以唯一比对 reads 为分母则约 84% 落入基因区。其余为多重比对（约 24%，短读长与基因家族同源性所致，属本数据正常水平）、基因间区与重叠歧义 reads。" },

  { k: "h2", t: "4.2 样本聚类与 PCA" },
  { k: "p", t: "对 vst 变换后的表达矩阵（前 500 高变基因）进行 PCA 分析（图 1）。PC1 轴上 Luminal 与 Basal 两类细胞截然分离，解释度最高的变异来自细胞类型而非发育时期；每个生物学重复与其同组样本紧密聚集，表明重复性良好。样本距离热图（图 2，前 1000 高变基因）给出相同结论：聚类树首先按细胞类型分叉，其次按时期聚集，未见离群样本。" },
  { k: "img", file: "PCA_plot.png", cap: "PCA 分析：按细胞类型与时期着色，PC1 清晰分离 Basal 与 Luminal", w: 500 },
  { k: "img", file: "sample_distance_heatmap.png", cap: "样本距离热图（Top 1000 高变基因）：同组重复聚为一类", w: 520 },

  { k: "h2", t: "4.3 差异表达统计与火山图" },
  { k: "p", t: "三组对比检出的差异表达基因（DEGs，padj < 0.05 且 |log2FC| > 1）统计如下表。细胞类型差异（8604 个 DEGs）数量级远高于发育时期差异（1943 与 3224 个），与原文“两类上皮细胞转录组本质不同”的结论一致。火山图（图 3–5）中，显著上调与下调基因清晰分离于两翼。" },
  { k: "table", widths: [2800, 1800, 1800, 2600],
    header: ["对比", "上调", "下调", "合计"],
    rows: [
      ["Luminal vs Basal", "3480", "5124", "8604"],
      ["pregnant vs virgin", "731", "1212", "1943"],
      ["lactating vs virgin", "1568", "1656", "3224"],
    ] },
  { k: "img", file: "DEG_summary.png", cap: "三组对比的 DEG 数量汇总", w: 430 },
  { k: "img", file: "volcano_Luminal_vs_Basal_results.png", cap: "火山图：Luminal vs Basal（差异最悬殊的一组）", w: 520 },
  { k: "img", file: "volcano_pregnant_vs_virgin_results.png", cap: "火山图：pregnant vs virgin", w: 520 },
  { k: "img", file: "volcano_lactating_vs_virgin_results.png", cap: "火山图：lactating vs virgin", w: 520 },

  { k: "h2", t: "4.4 Top 差异基因" },
  { k: "p", t: "Luminal vs Basal 最显著的 Top 20 差异基因表达热图（图 6）显示：标记基因在两类细胞间呈完全相反的表达模式，同一细胞类型内三个时期的表达则相对接近。全部 Top 50 差异基因列表已导出为 CSV 与 Markdown 表格（results/top50_*.csv/.md），完整统计结果见 results/*_results.csv。" },
  { k: "img", file: "top20_DEG_heatmap.png", cap: "Top 20 DEGs 表达热图（Z-score 标准化）：细胞类型间表达模式截然相反", w: 520 },

  // ---------- 五、讨论与局限 ----------
  { k: "h1", t: "五、讨论与局限" },
  { k: "p", t: "主要发现：Luminal 与 Basal 上皮细胞的转录组差异极为显著（8604 个 DEGs），是数据中占主导地位的变异来源；发育时期引起的表达变化相对温和，但泌乳期 vs 未孕（3224 个 DEGs）明显多于孕期 vs 未孕（1943 个），提示泌乳启动伴随更大的转录组重编程。该定性格局与 Fu et al.（2015）原文一致。" },
  { k: "p", t: "本分析的局限：其一，每条件仅 2 个生物学重复，统计功效有限，部分中等效应基因可能未达显著；其二，50 bp 单端短读长导致多重比对比例偏高（约 24%），损失了一部分可定量基因；其三，结果以 Ensembl gene_id 表示，尚未注释为基因符号，也未进行 GO/KEGG 功能富集分析；其四，DESeq2 设计仅含主效应，未建模交互项。" },
  { k: "p", t: "可改进方向：增加生物学重复；采用 Salmon / kallisto 伪比对加速流程；用 clusterProfiler 做功能富集以解释通路层面的变化；引入 gene symbol 注释提升结果可读性。" },

  // ---------- 六、问题排查记录 ----------
  { k: "h1", t: "六、问题排查记录" },
  { k: "p", t: "复现过程中记录的真实问题如下。这些案例覆盖了环境配置、版本兼容、数据解析与可视化四类典型故障，是本次训练的核心收获之一。" },
  { k: "table", widths: [2300, 3400, 3300],
    header: ["问题", "根因", "解决"],
    rows: [
      ["新版 Miniconda 无法安装", "服务器为 CentOS 7（glibc 2.17），新安装器要求 glibc ≥ 2.28", "改用 2023 年旧版安装包（py311_23.11.0）"],
      ["conda 镜像返回 403/404", "所选镜像源缺少 bioconda 频道或被限流（笔记本阶段）", "更换可用镜像；迁移服务器后直连官方源"],
      ["multiqc 启动报缺少 pkg_resources", "新版 setuptools 移除了该模块，而 multiqc 1.15 依赖它", "conda 安装 setuptools<70"],
      ["计算节点 pigz: command not found", "登录节点与计算节点系统软件不一致；且脚本未设严格模式，失败后继续打印“完工”造成假完工", "软件一律装进 conda 环境（随共享存储可用）；日志留哨兵行核对"],
      ["R 脚本找不到计数矩阵", "脚本按教程写作 counts_matrix.txt，实际产出为 count_matrix.txt；且 featureCounts 前 5 列为基因注释列，需先剔除", "修改路径；读入后只保留样本列"],
      ["样本名提取全部失败", "脚本按下切分文件名，实际文件名以点号连接（SRR1552444.Aligned...）", "改用按点号切分的正则"],
      ["火山图报 Axis limits cannot be NaN or Inf", "极显著基因 padj 数值下溢为 0，-log10(0) 为正无穷", "padj 截断下限 1e-300 后再取对数"],
      ["笔记本阶段反复 segfault / 文件损坏", "WSL 2 跨 9p 文件系统运行计算密集型工具不稳定", "整体迁移至实验室集群，根因消失"],
    ] },

  // ---------- 七、技能收获总结 ----------
  { k: "h1", t: "七、技能收获总结" },
  { k: "p", t: "通过本次复现，系统掌握了以下技能（按获得顺序）：" },
  { k: "b", t: "远程操作：SSH 密钥免密登录原理与配置、scp/sftp 文件传输、多客户端（PowerShell / WSL / 图形客户端）差异；" },
  { k: "b", t: "集群计算：PBS 作业脚本三段式、qsub/qstat 监控、资源申请三原则（核数跟工具、内存跟峰值、时间跟规模）、作业数组并行；" },
  { k: "b", t: "环境管理：conda 虚拟环境思想、channel 配置、CentOS 7 下的版本兼容策略；" },
  { k: "b", t: "流程工具：SRA Toolkit / FastQC / MultiQC / STAR / SAMtools / featureCounts / pigz 的职责与关键参数；" },
  { k: "b", t: "文件格式：FASTQ / FASTA / GTF / SAM-BAM / 计数矩阵的结构与相互关系；" },
  { k: "b", t: "统计方法：DESeq2 的建模思想（设计公式、标准化、离散度估计、Wald 检验、BH 校正）与 vst 变换；" },
  { k: "b", t: "排错能力：读 traceback 定位根因、区分“真完工”与“假完工”、日志哨兵与时间戳惯例；" },
  { k: "b", t: "Linux 基本功：bash 循环与变量、管道与 grep、nano 编辑、nohup 后台任务。" },

  // ---------- 附录 ----------
  { k: "h1", t: "附录 A  项目目录结构" },
  { k: "code", lines: [
    "~/GSE60450_project/",
    "├── reference/           基因组、GTF 与 STAR 索引（STAR_index 约 25 G）",
    "├── raw_data/            SRA 原始数据（12 个目录，约 22 G）",
    "│   └── fastq/           转换并压缩后的 FASTQ（12 个 .fastq.gz，约 30 G）",
    "├── alignment/           比对结果（每样本一个目录：BAM + Log.final.out 等）",
    "├── counts/              count_matrix.txt 及其 summary",
    "├── qc/                  fastqc 单项报告 + multiqc 汇总 HTML",
    "├── results/             DESeq2 全部输出（CSV / PDF / PNG / Markdown）",
    "├── results/figures/     火山图、MA 图、汇总图",
    "├── scripts/             全部 PBS 脚本与分析脚本（共 11 个）",
    "└── logs/                各作业的标准输出与报错日志",
  ] },
  { k: "h1", t: "附录 B  脚本清单" },
  { k: "table", widths: [2500, 4200, 2300],
    header: ["脚本", "用途", "提交方式"],
    rows: [
      ["overnight_download.sh", "过夜下载基因组与 12 个 SRA（nohup）", "bash + nohup"],
      ["star_index.pbs", "STAR 基因组索引（16 核 / 50 G）", "qsub"],
      ["sra2fq.pbs", "SRA 转 FASTQ 并压缩（4 核 / 16 G）", "qsub"],
      ["align.pbs", "STAR 比对作业数组（8 核 / 45 G × 12）", "qsub -t 0-11"],
      ["count.pbs", "featureCounts 基因定量（8 核 / 16 G）", "qsub"],
      ["qc.pbs", "fastqc 6 路并行 + multiqc 汇总", "qsub"],
      ["deseq.pbs", "DESeq2 差异分析（4 核 / 16 G）", "qsub"],
      ["viz.pbs", "火山图 / MA 图 / Top 表生成", "qsub"],
      ["heatmap_png.pbs", "热图 PNG 版补出（报告插图用）", "qsub"],
      ["deseq2_server.R", "DESeq2 分析主体（服务器适配版）", "Rscript"],
      ["visualization_server.py", "Python 可视化（服务器适配版）", "python3"],
    ] },
  { k: "note", t: "全部原始脚本与日志存放于服务器 ~/GSE60450_project/，本报告与插图另存于本地工作区。数据分析环境可凭 conda 环境定义文件一键重建。" },
);

// ================= 渲染器 =================
const render = (bs) => bs.flatMap((b) => {
  switch (b.k) {
    case "h1": return [h1(b.t)];
    case "h2": return [h2(b.t)];
    case "p": return [body(b.t)];
    case "b": return [bullet(b.t)];
    case "note": return [para(run(b.t, { size: 20, color: palette.light, italics: true }), { spacing: { after: 200, line: 280 } })];
    case "code": return codeParas(b.lines);
    case "table": return [makeTable(b.widths, b.header, b.rows)];
    case "img": return img(b.file, b.cap, b.w || 520);
    case "pb": return [pageBreak()];
    default: return [];
  }
});

// ================= 封面 =================
const cover = [
  para("", { spacing: { before: 2400 } }),
  para(hrun("GSE60450 小鼠乳腺 RNA-seq 数据分析复现报告", { bold: true, size: 40, color: palette.dark }), {
    alignment: AlignmentType.CENTER, spacing: { after: 240 },
  }),
  para(run("从原始数据到差异表达的全流程实现（基于实验室 PBS 集群）", { size: 26, color: palette.primary }), {
    alignment: AlignmentType.CENTER, spacing: { after: 600 },
  }),
  para(run("数据集：GEO GSE60450 / SRA SRP045534（Fu et al., 2015）", { size: 22 }), {
    alignment: AlignmentType.CENTER, spacing: { after: 120 },
  }),
  para(run("分析平台：实验室集群 ds01 + cn1（PBS 调度）", { size: 22 }), {
    alignment: AlignmentType.CENTER, spacing: { after: 120 },
  }),
  para(run("完成日期：2026 年 9 月 27 日", { size: 22 }), {
    alignment: AlignmentType.CENTER, spacing: { after: 1200 },
  }),
  para(run("本报告为本科毕业设计课题的实验记录底稿：涵盖计算环境、流程方法、质控与差异分析结果、问题排查记录，全部步骤可在集群上凭脚本与日志复现。", { size: 20, color: palette.light, italics: true }), {
    alignment: AlignmentType.CENTER, spacing: { after: 240 },
  }),
  pageBreak(),
  para(hrun("目录", { bold: true, size: 30, color: palette.dark }), {
    heading: HeadingLevel.HEADING_1, spacing: { before: 240, after: 160 },
  }),
  para(run("（如页码与正文不符，在 Word 中右键目录选择“更新域”即可刷新）", { size: 18, color: palette.light, italics: true }), {
    spacing: { after: 160 },
  }),
  toc(TOC_ENTRIES),
  pageBreak(),
];

// ================= 文档组装 =================
const doc = new Document({
  features: { updateFields: true },
  numbering: {
    config: [
      {
        reference: "bullet",
        levels: [
          {
            level: 0,
            format: LevelFormat.BULLET,
            text: "•",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 560, hanging: 280 } } },
          },
        ],
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1300, right: 1300 },
        },
        titlePg: true,
      },
      headers: {
        default: new Header({
          children: [para(run("GSE60450 小鼠乳腺 RNA-seq 复现报告", { size: 18, color: palette.light }), {
            alignment: AlignmentType.CENTER, spacing: { after: 60 },
          })],
        }),
        first: new Header({ children: [para("", {})] }),
      },
      footers: {
        default: new Footer({
          children: [para(new TextRun({ children: [PageNumber.CURRENT] }), { alignment: AlignmentType.CENTER })],
        }),
        first: new Footer({ children: [para("", {})] }),
      },
      children: [...cover, ...render(blocks)],
    },
  ],
});

const buffer = await Packer.toBuffer(doc);
fs.writeFileSync(outputPath, buffer);
console.log("written:", outputPath);
