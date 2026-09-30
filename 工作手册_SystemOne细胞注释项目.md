# 工作手册：System One 决策模型单细胞注释项目（AI 执行版）

> 本文档是完整的项目执行手册。执行者（AI）应按阶段顺序执行，每阶段完成后对照验收标准自检。
> 所有事实性声明均已核实，引用来源见第 9 节。

---

## 0. 项目背景（执行前必读）

**目标**：系统评测 System One 决策模型（2026 年 9 月发布的新模型类别）用于单细胞 RNA-seq 细胞类型注释的可行性，与生成式 LLM 和传统工具对比，产出可复现的基准结果和一个两级注释管线工具。这是一个个人求职项目，最终交付物是 GitHub 仓库 + 对比数字 + README。

**什么是 System One 模型**：非生成式决策模型。输入一个 `state`（文本/JSON 描述）+ 一组 `questions`（Choice 选择题 / Score 评分 / Noul 是非题），在一次前向传播中返回类型化答案 + 校准概率。不开源的商业版是 TypeSafe AI 的 Jev（$0.042/M input tokens，国内付费困难，本项目不用）；开源复刻版有 Kev（Qwen3.5 底座，零样本准确率 0.852）和 Laya（ModernBERT 底座 421M，零样本仅 0.362 接近随机，必须微调）。统一接口契约为 POST `/v1/systemone`，请求体 `{state, questions}`。

**细胞注释任务建模**：聚类级注释。每个 cluster 提取 top 30 marker 基因作为 state，候选细胞类型词表作为 Choice 选项，模型返回各类型概率。置信度低于阈值的 cluster 升级给 LLM 复核（两级架构）。

**方法论来源**：DeepCellSeek（Xiao et al., Briefings in Bioinformatics, 2025, https://academic.oup.com/bib/article/26/6/bbaf677/8380197 ）。其最佳实践：Wilcoxon 秩和检验筛 marker、按统计显著性+log2FC 排序、每 cluster 输入 30 个 marker。本项目的差异化：DeepCellSeek 测的是生成式 LLM，本项目测 System One 决策模型，并做词表规模/层级注释/置信度门控三组消融。

---

## 1. 硬件与存储布局（已实测，不要更改）

- 机器：Windows 笔记本，i5-12450H / RTX 4050 Laptop 6GB 显存 / 16GB RAM
- C 盘（NVMe SSD，28GB 空闲）：Python 环境 + 代码仓库，预算 ≤9GB
- E 盘（移动机械硬盘 KESU，191GB 空闲）：模型缓存 + 数据集 + 微调 checkpoint
- ⚠️ E 盘是移动硬盘：Python 虚拟环境和代码严禁放 E 盘（小文件随机读写极慢）；使用中途不可拔盘；微调产出的权重需备份到网盘

## 2. 四条对比路线

| 编号 | 模型 | 运行位置 | 角色 |
|---|---|---|---|
| R1 | Laya（convaiinnovations/laya，421M，Apache-2.0） | 本地 CPU | System One 微调组 |
| R2 | Kev-4B（jaredpalmer/kev，4-bit 量化，Apache-2.0） | 本地 GPU | System One 零样本组 |
| R3 | DeepSeek API（备选 Qwen/智谱 GLM，国内可付费） | API | LLM 对照组 |
| R4 | SingleR 或 CellTypist | 本地 | 传统工具对照组 |

控制变量：所有路线使用完全相同的聚类结果和 marker 列表，只更换判断者。

## 3. 阶段任务与验收标准

### 阶段 0：环境搭建（预计 0.5 天）

任务：
1. 创建目录结构：
   - `C:\cell-annotation\` —— 代码仓库（git init）
   - `E:\cell-annotation\hf_cache\` —— Hugging Face 缓存
   - `E:\cell-annotation\datasets\` —— 数据集
   - `E:\cell-annotation\checkpoints\` —— 微调输出
2. 设置持久环境变量：`setx HF_HOME "E:\cell-annotation\hf_cache"`
3. 在 C 盘创建 Python 3.11+ 虚拟环境，安装：
   - `torch`（CUDA 12.x 版）、`transformers`、`accelerate`
   - `scanpy`、`anndata`、`leidenalg`
   - `laya`（pip install laya）
   - `requests`、`pandas`、`scikit-learn`、`matplotlib`、`seaborn`
4. 下载 Laya 权重到 E 盘缓存，发一个测试请求验证通路：
   ```python
   # state: "Human PBMC scRNA-seq cluster. Top markers: CD3D, CD3E, CD8A, CD8B, GZMK, CCL5, NKG7"
   # question: choice, options: ["CD8 T cell","CD4 T cell","B cell","NK cell","Monocyte"]
   ```
5. 下载 Kev-4B 量化版，验证 GPU 推理（torch.cuda.is_available() 为 True）

验收：Laya 和 Kev-4B 各自成功返回一个带概率分布的 Choice 答案；C 盘占用 ≤9GB；模型文件确认落在 E 盘。

### 阶段 1：数据与基线（预计 2–3 天）

任务：
1. 下载 PBMC 3K（scanpy 内置 `sc.datasets.pbmc3k()`）及金标准注释（10x Genomics 官方注释或 scanpy tutorial 的 reference 注释）
2. Scanpy 标准流程：质控（过滤基因数/线粒体比例）→ 归一化 → 高变基因 → PCA → 邻接图 → Leiden 聚类 → UMAP
3. marker 提取：`sc.tl.rank_genes_groups(groupby='leiden', method='wilcoxon')`，按 p 值 + log2FC 排序，每 cluster 取 top 30
4. 构建候选词表：PBMC 参考细胞类型（CD4 T, CD8 T, NK, B, CD14+ Monocyte, FCGR3A+ Monocyte, DC, Platelet 等）+ "unknown" 兜底项
5. R3 基线：调 DeepSeek API，prompt 仿照 DeepCellSeek（输入物种+组织+top30 marker，要求输出词表内单一类型），记录每 cluster 结果与 API 成本
6. R4 基线：CellTypist（默认 PBMC 参考模型）跑同批 cluster

验收：两条基线在 PBMC 3K 上的完美匹配率（对照金标准）与 DeepCellSeek 论文报告的量级一致（LLM 路线应在 70–85% 区间）；结果存为 CSV：`cluster_id, gold_label, r3_label, r4_label, r3_cost_usd, r3_latency_ms`。

### 阶段 2：System One 接入（预计 3–4 天）

任务：
1. R2 零样本：对同一批 cluster，构造 state（英文，含物种/组织/top30 marker 基因名），Choice 题调用 Kev-4B，记录概率分布和置信度
2. R1 微调数据构建：从 PBMC 3K 金标准生成 JSONL，每 cluster 一条：
   ```json
   {"state": "...", "question": "Which cell type is this cluster?", "options": [...], "answer": "..."}
   ```
   另从 CELLxGENE 或 DeepCellSeek 论文附录再取 1–2 个带注释的小数据集扩充；目标每类 ≥20–50 条；按 cluster 划分 train/val/test（8:1:1），防泄漏
3. Laya 微调：用官方微调笔记本（GitHub: NandhaKishorM/laya），在 Kaggle 免费 T4×2 或 AutoDL 上跑 3 epochs，lr 2e-5，约 4–5 小时；产出 checkpoint 存 E 盘并备份网盘
4. 温度校准：在 val 集上 fit_temperature，记录 ECE 前后对比（出厂约 0.466，目标 ≤0.1）
5. R1 在 test 集上评测

验收：R1、R2 结果并入对比 CSV；微调后 Laya 准确率显著高于其零样本（0.362 量级）；校准后 ECE ≤0.1。

### 阶段 3：消融实验（预计 2–3 天）

- A1 词表规模：候选数 10 / 25 / 50 / 100 × {R1, R2, R3}，画准确率-词表规模曲线（预期：决策模型随词表增大下降更陡，尤其 Laya 在 >20 选项后骤降）
- A2 层级 vs 扁平：先问大类（lymphoid/myeloid/platelet）再递归亚型，对比扁平 100 类
- A3 置信度门控：以 R2 置信度为门控，画准确率-覆盖率曲线；低置信 cluster 升级 R3 复核，报告两级架构的整体准确率和成本节省倍数

验收：三组消融各产出一张图 + 一段结论；全部数字可复现（随机种子固定，代码入库）。

### 阶段 4：封装与交付（预计 2–3 天）

1. 管线封装为 CLI：`annotate --input h5ad --router kev4b --threshold 0.8 --fallback deepseek`
2. README：项目动机、架构图、对比表格（准确率/宏F1/ECE/延迟/成本）、消融结论、踩坑记录（必须包含：Laya 零样本接近随机、选项 >20 性能骤降、出厂校准差这三点及应对）
3. 最终交付清单：代码仓库、结果 CSV、全部图表、本手册中第 8 节简历表述的 XX 替换为真实数字

## 4. 关键代码约定

- 所有 System One 调用封装为统一函数 `systemone_decide(state, options, backend)`，backend ∈ {laya, kev, jev}，内部走 `/v1/systemone` 契约，便于未来换 Jev 只改 base_url
- 随机种子全局固定 42；所有结果落盘 CSV/JSON，不依赖内存状态
- 费用记录：每次 API 调用记录 token 数和成本

## 5. 已知的坑（执行中遇到先查这里）

1. **Laya 零样本不可用**：准确率 0.362 vs 随机 0.318，别浪费时间调试零样本 Laya，直接走微调
2. **Laya 上下文极小**：英文版总 512 token，state 预算 ~320 token，选项 ~192 token。marker 只放基因名（30 个 ≈ 60–90 token），不要放长描述；选项 >20 时改用层级注释
3. **Laya 英文 checkpoint 对非英文会静默崩溃**（置信度 0.95 但全错）：所有 state 必须用英文
4. **校准不可省略**：出厂 ECE 0.466，任何基于置信度的门控必须先做温度缩放
5. **E 盘盘符漂移**：若模型突然要重新下载，先检查移动硬盘盘符是否还是 E
6. **Git Bash 调 PowerShell 时 `$_` 会被吞**：写 .ps1 文件再执行，不要内联
7. **HF 下载慢**：可设 `HF_ENDPOINT=https://hf-mirror.com`（国内镜像）

## 6. 数据集清单

| 数据集 | 用途 | 获取 |
|---|---|---|
| PBMC 3K (10x) | 主验证集 + 微调数据来源 | `sc.datasets.pbmc3k()` |
| PBMC 10K 或 DeepCellSeek 论文附录中 1–2 个小数据集 | 泛化验证 + 微调扩充 | CELLxGENE / 论文附录 |

## 7. 时间线

总周期约 4–5 周（业余时间）：阶段 0–1 第 1 周，阶段 2 第 2–3 周，阶段 3 第 3–4 周，阶段 4 第 4–5 周。

## 8. 最终简历表述模板

> 独立设计并实现基于 System One 决策模型的单细胞 RNA-seq 注释基准，对比 4 条技术路线（含微调 421M 开源决策模型），在 PBMC 基准上达到 XX% 完美匹配率，两级门控架构使注释成本降低 XX 倍；全程在 16GB 内存 + 6GB 显存笔记本上完成，代码与数据可复现。

## 9. 事实来源（均已核实）

- Jev 发布与规格：https://github.com/Raunaksplanet/jev-research-sept-2026
- Laya 模型卡与零样本局限：https://huggingface.co/convaiinnovations/laya ，https://aiweekly.co/alerts/convai-ships-laya-a-421m-modernbert-decision-model-apache-20
- Laya 详细规格（上下文预算、Router、微调笔记本）：https://systemonemodels.org/models/laya/
- 开源替代对比（Kev 0.852 等）：https://rohitraj.tech/notes/jev-alternatives-open-weights-decision-models-2026
- DeepCellSeek 论文与最佳实践：https://academic.oup.com/bib/article/26/6/bbaf677/8380197 ，http://kiz.cas.cn/GEAM/geam2/geam21/202601/t20260107_8096680.html
