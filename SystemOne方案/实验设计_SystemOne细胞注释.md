# 实验设计：System One 决策模型用于单细胞类型注释的系统评测

> 项目定位：个人求职项目。借鉴 DeepCellSeek（Xiao et al., *Briefings in Bioinformatics*, 2025）的基准方法论，
> 将其扩展到 2026 年 9 月新发布的 System One 决策模型范式，产出可量化的对比结论。

## 1. 研究问题

**核心问题**：System One 决策模型（非生成式、带校准置信度的选择题模型）能否胜任单细胞 RNA-seq 的细胞类型注释？与生成式 LLM 和传统工具相比，在准确率、成本、延迟上的权衡如何？

**子问题**：
1. 零样本决策模型（Kev-4B）与微调小模型（Laya）的差距有多大？
2. 候选词表规模对决策模型准确率的影响（决策模型特有的结构性约束）？
3. 层级注释（先大类后亚型）能否缓解词表规模问题？
4. 校准置信度用于门控（低置信升级 LLM 复核）的两级架构，能否兼顾成本与准确率？

## 2. 对比路线（自变量）

| 编号 | 路线 | 类型 | 预期角色 |
|---|---|---|---|
| R1 | **Laya**（421M，本地 CPU） | System One，微调 | 主角：微调实验对象 |
| R2 | **Kev-4B**（本地 GPU 4050） | System One，零样本 | 主角：零样本基线 |
| R3 | **DeepSeek API**（或 Qwen，国内可付费） | 生成式 LLM | 对照：DeepCellSeek 路线代表 |
| R4 | **SingleR / CellTypist** | 传统工具 | 对照：传统基线 |
| R5（可选） | Jev via OpenRouter | System One 官方 | 上限参照（若解决付费） |

控制变量：所有路线使用**完全相同的聚类结果和 marker 提取流程**（Wilcoxon 秩和检验 + log2FC 排序，每 cluster top 30 marker，遵循 DeepCellSeek 最佳实践），只更换"判断者"。

## 3. 数据集

| 数据集 | 用途 | 来源 |
|---|---|---|
| **PBMC 3K**（10x Genomics） | 主验证集，金标准注释现成 | 10x / Scanpy 内置 |
| PBMC 10K 或 DeepCellSeek 论文清单中的 2–3 个小型数据集 | 泛化验证 | DeepCellSeek 论文附录 / CELLxGENE |
| PBMC 3K 的 cluster-marker 标注对 | Laya 微调训练集（train/val/test 划分） | 自构建 |

规模控制在 3–5 个数据集，确保受控对比的严谨性优先于覆盖面。

## 4. 实验流程

### 阶段 0：环境搭建（第 1 周）
- C 盘：Python 虚拟环境 + 代码仓库
- E 盘（移动硬盘）：`HF_HOME=E:\cell-annotation\hf_cache`，数据集目录
- 下载 Laya、Kev-4B，跑通 `/v1/systemone` 契约的第一个注释请求

### 阶段 1：基线复现（第 1–2 周）
- Scanpy 标准流程：质控 → 归一化 → Leiden 聚类 → `rank_genes_groups`（Wilcoxon，top 30）
- 用 R3（DeepSeek API）复现 DeepCellSeek 式注释，与论文/金标准数字对照，验证实验装置可信

### 阶段 2：System One 接入（第 2–3 周）
- R2 零样本注释：state = 物种+组织+top30 marker；Choice 题，候选词表从 Cell Ontology/PBMC 参考集构建（含 unknown 兜底）
- R1 微调：构建 JSONL 训练集（每 cluster 一条：state + options + answer），Kaggle/AutoDL 免费 GPU 微调 4–5h，回本地做温度校准（temperature scaling）

### 阶段 3：消融实验（第 3–4 周，原创发现点）
- A1 词表规模：10 / 25 / 50 / 100 个候选类型 × 各模型准确率
- A2 层级 vs 扁平：先分 immune/stromal/epithelial 大类，再递归亚型
- A3 置信度门控：画准确率-覆盖率曲线，定阈值；低置信 cluster 升级 R3 复核，测两级架构的成本-准确率权衡

### 阶段 4：整理输出（第 4–5 周）
- 结果表格 + 混淆矩阵 + 校准曲线
- 封装 Python 包 / CLI，README 写清方法、数字、踩坑记录
- 可选：技术博客或 bioRxiv 预印本

## 5. 评估指标

| 指标 | 说明 |
|---|---|
| 完美匹配率（exact match） | 与金标准完全一致的比例，主指标，对齐 DeepCellSeek |
| 宏 F1 | 处理类别不平衡 |
| ECE（校准误差） | 置信度可信度，温度校准前后对比 |
| 单 cluster 延迟 | 各路线实测 |
| 单 cluster 成本 | API 计费 / 本地折算 |
| 准确率-覆盖率曲线 | 门控架构的核心证据 |

## 6. 硬件与资源约束（已实测本机）

- i5-12450H / RTX 4050 Laptop 6GB / 16GB RAM / C 盘 28GB 空闲
- E 盘移动机械硬盘 191GB：模型缓存 + 数据集
- Laya → CPU 跑；Kev-4B → GPU 跑；Kev-9B 不装
- 微调算力：Kaggle 免费 T4 ×2 或 AutoDL（几元/小时）

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Laya 零样本接近随机（0.362），微调是硬依赖 | 训练集从金标准数据集构建；每类 ≥20–50 条 |
| Laya 选项 >20 准确率骤降、state 预算 ~320 token | 层级注释；marker 精简到 top 20–30 |
| 出厂校准差（ECE 0.466） | 验证集温度缩放 → 0.081 级别 |
| 移动硬盘盘符漂移 / 故障 | 固定盘符；权重云端备份 |
| 范围蔓延 | 锁定 3–5 个数据集，阶段 3 的消融最多做 3 组 |

## 8. 预期产出（简历素材）

- 一个 GitHub 仓库：完整可复现的基准 + 两级注释管线工具
- 核心数字：各路线准确率/成本/延迟对比表、微调前后提升幅度、门控架构的成本节省倍数
- 一句话简历表述：*"独立设计并实现基于 System One 决策模型的单细胞注释基准，对比 4 条技术路线，在 PBMC 基准上达到 XX% 准确率，注释成本降低 XX 倍（16GB 内存笔记本全程复现）"*
