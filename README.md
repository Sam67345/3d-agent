# 毕设 MVP：3D 插值 Agent 最小可行原型

本科毕设《面向空间转录组三维插值的智能体系统设计与实现》。

- 命令行优先的多智能体原型：自然语言指令 → Planner / Skill 注册表 / Verifier / Executor+Error Reflector → SpatialZ 三维插值 → plotly 可视化 + 可复现 notebook
- 核心算法：SpatialZ（Nature Methods 2026, 23(2):360-372；源码 Zenodo: https://zenodo.org/records/17416727 ；教程 https://spatialz-tutorial.readthedocs.io/ ）
- 架构参照 STAT（bioRxiv 2026.05.01.722244）精简版

## 环境

| 项目 | 值 |
|---|---|
| 系统 | Windows 11 家庭版，Acer Nitro AN515-58 |
| CPU | i5-12450H（8 核 12 线程），16 GB 内存 |
| GPU | RTX 4050 Laptop 6GB（备用，MVP 全程 CPU 可跑） |
| Python | 3.12.6（用户安装版），项目独立虚拟环境 `.venv/` |
| 关键依赖 | scanpy 1.12.4, squidpy 1.8.3, anndata 0.13.4, plotly 7.1.0, torch(CPU), POT 0.9.7, MENDER 1.1, jupyter, nbformat, python-dotenv |
| LLM | DeepSeek（OpenAI 兼容，`deepseek-flash`），配置在项目根目录 `.env`（不提交版本库） |

激活环境（Windows）：

```bat
.venv\Scripts\activate
```

## LLM 配置（M4 才用到）

代码通过 OpenAI 兼容接口调用 LLM，**密钥只读环境变量，不写死**：

- `MY_LLM_API_KEY`
- `MY_LLM_BASE_URL`
- `MY_LLM_MODEL`

当前状态（2026-09-30）：三个变量均未设置，M1–M3 不需要 LLM，M4 开始前必须配置。

## 数据

DLPFC 10x Visium 连续切片，取相邻 2 张：

| 文件 | 说明 |
|---|---|
| `data/151673_filtered_feature_bc_matrix.h5ad` | 切片 151673（120 MB） |
| `data/151674_filtered_feature_bc_matrix.h5ad` | 切片 151674（135 MB） |
| `data/151675_filtered_feature_bc_matrix.h5ad` | 切片 151675（108 MB，M2.5 held-out 真值） |

- 来源：Zenodo 记录 22685654（DOI: 10.5281/zenodo.22685654，SNMF 论文配套数据），其中 h5ad 源自 spatialLIBD（Maynard et al. 2021）
- 151673 与 151674 为同一供体的相邻连续切片（spatialLIBD 12 张切片编号 151507–151676）
- 每张切片：约 3.6k spot × 33,538 基因，稀疏矩阵（csr），空间坐标在 `obsm['spatial']`（像素坐标，x=col，y=row）
- 人工皮层分层注释在 `obs['Region']`（Layer1–Layer6 / WM），缺失值（切片边缘 spot）加载时丢弃
- 磁盘占用约 255 MB，远低于 5 GB 约束

备用数据（M2.5 之后追加，用于论文其他实验）：

| 文件 | 说明 |
|---|---|
| `data/merfish_five/merfish_{4,9,14,19,24}_paste.h5ad` | SpatialZ 论文五张 MERFISH 下丘脑切片（Moffitt et al.），每张约 5.5k–5.9k 细胞 × 155 基因，含 `cell_class` 注释与 `obsm['spatial']` |
| `data/starmap/STARmap_Wang2018three_data_3D_data.h5ad` | STARmap 官方注释版基础文件（32,845 × 28，含 leiden 聚类；cell_class 需自行重建） |
| `data/starmap/starmap_3d_raw.h5ad` | STARmap 小鼠视皮层 3D 体积（28 基因 × 33,598 细胞，z=6–94），txt 原始文件同目录 |
| `data/starmap/STARmap_3D_data_{expression,cell_locations}.txt` | STARmap 原始 txt（基因×细胞表达矩阵 + 3D 坐标） |
| `reference/gener_five_merfish.ipynb` | SpatialZ 官方五张 MERFISH 切片生成 notebook（Zenodo 源码包内） |

- 注意：论文 STARmap 实验本体是一个 3D 体积（教程切成 7 层用），"五张切片" 指 MERFISH 数据集；两者都已下载备用
- STARmap 原始来源：Wang et al. Science 2018（starmapresources.org 本机网络 403，改用 GiottoData 镜像 drieslab/spatial-datasets）；MERFISH 五切片来自 Zenodo 17416727 源码包

## 运行方式

```bat
.venv\Scripts\python m1_load_data.py
```

## 里程碑日志

### M1 环境 + 数据加载（2026-09-30 完成）

**验收标准**：能加载 DLPFC 切片并打印数据概况（spot 数、基因数、坐标范围）。✅ 通过。

运行结果：

| | 151673 | 151674 |
|---|---|---|
| spot 数 | 3,611 | 3,635 |
| 基因数 | 33,538 | 33,538 |
| 坐标 x 范围 | [3096, 11062] | [2967, 10900] |
| 坐标 y 范围 | [2437, 11195] | [2505, 11617] |
| 分层注释 | Layer1–6 + WM，各层 218–989 个 spot | 同左，各层 224–925 个 spot |

**决策记录**：

1. **虚拟环境**：用用户自装 Python 3.12.6 在项目目录建 `.venv`，不污染 Kimi 运行时内部环境。
2. **pip 源**：本机网络环境下清华镜像（tuna）返回 403、阿里云镜像超时，**官方 pypi.org 可用且速度快**，安装统一走官方源。

**踩坑记录**：

1. `truth.txt` 无表头（首行会被 pandas 误吃为列名）→ 用 `header=None` 读取。
2. 同数据源两张切片的 truth.txt 编码不一致（151673 为 ASCII/UTF-8，151674 为 UTF-16LE）→ 曾加编码自动探测。
3. Zenodo 上 `151674_truth.txt` 源文件本身全为 0x00（损坏），两个下载端点重下均如此 → **弃用 truth.txt**，改用 h5ad 自带的 `obs['Region']`（经核对，151673 的 Region 与 truth.txt 逐层计数完全一致）。
4. pandas 读含 NaN 的字符串列时 `.str` 访问器报 `AttributeError` → 显式 `dtype=str`。

### M2 手工跑通 SpatialZ（2026-09-30 完成）

**验收标准**：不用 agent，脚本直接调 SpatialZ 完成 2 张切片间插值，输出虚拟切片数据。✅ 通过。

**SpatialZ 真实流程**（读源码 `SpatialZ.py` 确认，与交接包"四步"略有出入，实为 5 个阶段）：

1. 空间坐标生成：从两张真实切片按 alpha 比例均匀抽样初始化 → Sliced Wasserstein 重心梯度下降（`ot.sliced_wasserstein_distance`，torch 张量）——对齐与插值的核心
2. 细胞类型判定：新坐标对两切片 kNN（k=1），多数投票
3. 属性转移（`add_obs_list`）
4. 基因表达合成：`syn_mode='default'` = MENDER 微环境度量 + 逐基因加权抽样（为 merfish 155 基因设计，对 Visium 3.3 万基因会退化为 1.2 亿次 Python 循环，**不可用**）；`syn_mode='fast'` = 10-NN 同类型均值（向量化）
5. 合并输出 real + synthetic 的 dense AnnData

**运行结果**（CPU，i5-12450H，每层约 114 s，其中 OT 优化 106 s）：

- 5 层虚拟切片（alpha = 1/6 … 5/6），各 3,615–3,630 spots × 3,436 基因（两切片 top-2000 HVG 并集）
- 输出：`output/virtual_alpha{0.167…0.833}.h5ad`，合并版 `output/dlpfc_dense_5sim.h5ad`（25,359 spots × 3,436 genes，~340 MB）
- 验证 1：分层构成随 alpha 从 151674 构成平滑过渡到 151673 构成（如 WM 比例 0.172 → 0.142，Layer6 0.169 → 0.192）
- 验证 2：相邻层全基因平均表达 Spearman 相关 ρ ≥ 0.994，越靠近真实切片相关性越高
- 验证 3：虚拟切片坐标范围落在两张真实切片之间

**关键决策**：

1. **弃用 requirements.txt 的版本钉死**：其 numpy==1.22.2 / anndata==0.9.1 / scanpy==1.9.3 等在 Python 3.12 无 wheel。实测 SpatialZ 源码在 numpy 2.x / anndata 0.13 / scanpy 1.12 下仅需 4 处小改（见下）。
2. **CPU 版 PyTorch**：满足"全程 CPU 可跑"硬约束（教程用 CUDA 11.7 + torch 1.13，device='auto'/'cpu' 均可跑通，CPU 已验证）。
3. **syn_mode='fast' + HVG 面板**：default 模式对 33,538 基因的 Visium 数据计算上不可行；改用 fast 模式 + 每切片 top-2000 HVG 并集（3,436 基因），符合空间组学标准实践。
4. **cell_type_key 用 `obs['Region']`**：Visium 是 spot 级无单细胞类型，用 7 类皮层分层注释代替，SpatialZ 按 spot 为单位插值（其原生设计为单细胞分辨率，此为本 MVP 的适配点，论文中需说明）。
5. **逐层独立运行**：每次 Bash 调用生成一层，避免 300 s 超时，也便于定位错误。
6. **SOMENDER 安装**：PyPI 上 pip 默认装 sdist 会因旧 setuptools + Python 3.12 失败（`pkgutil.ImpImporter` 已移除），直接下载官方 wheel 安装成功。

**对 SpatialZ.py 的 4 处机械修复**（项目根目录副本，原始 zip 保留在 `spatialz_src/`）：

1. 第 127 行：`AnnData(..., dtype=np.float32)` → anndata 0.13 已删除 dtype 参数，删除之
2. 第 231/274/324 行：`AnnData.concatenate(...)` → `ad.concat(...)`（anndata 0.13 已删除 concatenate）
3. 第 331-332 行（fast 合成）：pandas 新版禁止 `series[int]` 位置索引 → 预取 `.values` numpy 数组

**踩坑记录**：

- `sc.pp.highly_variable_genes` 参数名是 `n_top_genes` 不是 `n_top`
- Generate_spatialz 会原地修改输入 adata 的 obs_names（追加切片 ID 后缀），多次调用会叠加——不影响计算，但 M3 封装时需告知
- 坐标对齐说明：SpatialZ 的"对齐"是把两张切片当作同一坐标系下的分布做 OT 重心，**不做刚性配准**；DLPFC 相邻切片像素坐标天然接近，直接可用

### M3 技能封装（2026-09-30 完成）

**验收标准**：SpatialZ 流程封装为 skills/ 下的技能文件，agent 能识别并调用。✅ 通过。

`skills/` 目录（每个技能 = 一个 YAML 头 Markdown 技能文件 + 一个同名 Python 实现模块）：

| 技能 | 实现 | 说明 |
|---|---|---|
| load_slices | `skills/load_slices.py` | 载入两张 h5ad + 归一化 + HVG 面板（复用 m1/m2 函数） |
| interpolate | `skills/interpolate.py` | 指定 alpha 生成虚拟切片（复用 `m2_interpolate.gen_one`，新增 `syn_mode` 参数透传） |
| validate | `skills/validate.py` | 三项质量检查，返回结构化 dict（复用 `m2_validate.run_validation`） |
| visualize_3d | `skills/visualize_3d.py` | plotly 3D 散点（每层一个 z、按 Region 着色）→ `output/view_3d.html` |

每个 YAML 技能文件含 `name / description / inputs（名称、类型、必填、合法范围）/ preconditions（Verifier 逐项检查清单）/ outputs（返回结构）/ usage_example`（示例代码均抄自 m2 实测脚本）。

**单元测试**：`tests/test_m3_skills.py` 共 21 项全部通过（含非法参数报错、缺前置条件报错等负例）。

**重构记录**（未改写逻辑，只做可调用化）：

1. `m2_interpolate.gen_one` 增加 `syn_mode` 参数透传（默认 'fast' 不变）
2. `m2_validate` 的 main() 验证逻辑提取为可导入函数 `run_validation(order, names) -> dict`，main() 改为打印包装

**实现注意**：

- plotly 的 `Scatter3d` 本身即 WebGL 渲染（`Scattergl` 仅支持 2D，初版误用已修正）；超大数据量（>max_points，默认 5 万）按层等比例抽样
- z 轴层序按物理位置排列（2026-09-30 修复）：真实切片（data_type='real'）按 slice_id 排序排两端，虚拟切片按 alpha 从大到小排中间（alpha 从 obs['alpha'] 或 slice_id 正则 `alpha(\d+\.?\d*)` 提取）；无 data_type 列回退字母序并打印警告。原实现按字母序导致虚拟层全部堆在真实层上方
- 技能模块通过 `sys.path.insert(0, 项目根目录)` 复用 m1/m2 函数；import `m2_interpolate` 会连带加载 torch/SpatialZ（约数秒），load_slices 技能不受影响仅启动稍慢

### M4 Agent 闭环（2026-09-30 完成，LLM 实测待密钥）

**验收标准**：终端里用自然语言完成"加载→对齐→插值→可视化"全流程，有前置检查与错误处理演示。✅ 通过（MockLLM 链路实测；真实 LLM 只差环境变量，接口已按 OpenAI 兼容实现并封装完毕）。

组件（`agent/` 包 + `agent_cli.py` 入口，入口不能叫 agent.py——与包名冲突）：

| 组件 | 文件 | 职责 |
|---|---|---|
| LLM 客户端 | `agent/llm.py` | OpenAI 兼容 chat/completions，密钥只读 `MY_LLM_API_KEY/BASE_URL/MODEL`（支持 .env），带边界重试；`MockLLM` 供无密钥测试 |
| Planner | `agent/planner.py` | 自然语言 → JSON 步骤计划（只选技能+填参数，越权参数机械过滤） |
| Verifier | `agent/verifier.py` | 按技能 YAML preconditions 逐项检查，缺失则提问澄清，禁止编造 |
| Executor + Error Reflector | `agent/executor.py` | 执行技能；错误分类：机械（FileNotFound/KeyError/ValueError/TypeError）→ LLM 建议修复自动重试一次；结构性 → 诚实报告停止 |
| Session | `agent/session.py` | 全部中间状态单一持久化对象，pickle 落盘可恢复，记录 notebook 原料 |
| 注册表 | `agent/registry.py` | 解析 skills/*.md YAML 头 + 内置技能（overview/make_dense/export_notebook） |
| 导出器 | `agent/exporter.py` | Session 日志 → 可复现 notebook（M5） |

**闭环实测记录**（`python agent_cli.py --mock`，六类指令全通过）：

- "加载数据" → load_slices ✅ 返回两切片 meta（3611/3635 spots × 3436 基因）
- "查看数据概况" → overview ✅
- "在两张切片之间插值两层" → interpolate ×2（alpha=0.333/0.667，nb_iter=50）✅ 各 15 s
- "生成3D可视化" → **自动补全 make_dense 依赖** → visualize_3d ✅ 14,491 spots × 4 层 HTML
- "验证插值质量" → validate ✅ 相邻层 Spearman ρ = 0.995/0.998/0.991
- "导出notebook" → export_notebook ✅ `output/reproducible.ipynb`（16 单元，nbformat 校验合法）
- "在两张切片中间插值一层"（未加载时）→ **Verifier 拦截**："尚未加载切片对（需先执行 load_slices）" ✅

**单元测试**：`tests/test_m4_agent.py` 10/10 通过（Verifier 拦截、机械错误修复重试成功、重试有界、修复失败诚实报告、结构性错误直报、notebook 导出结构合法）。

**可靠性设计落地**（对应交接包"可靠性来自架构"）：

1. 依赖规范化在 handle 层自动补 make_dense，不依赖 prompt 技巧
2. 重试有界（MAX_RETRY=1），错误分两类处理
3. 所有执行成功/失败都写 Session.logs，可审计、可复现

**踩坑记录**：

- CLI 入口命名 `agent.py` 会遮蔽 `agent/` 包（ModuleNotFoundError），改名 `agent_cli.py`
- MockLLM 关键词匹配曾命中 Planner 拼接的状态文本（"已加载"），修正为只匹配用户指令行
- 技能 YAML preconditions 带中文注释导致精确匹配失效，Verifier 改为前缀匹配
- interpolate.md 最初漏写 preconditions，导致依赖检查下沉到 Executor 才报错——技能文件的 preconditions 必须完整

### M5 输出（2026-09-30 完成）

**验收标准**：3D 可视化 HTML + 可复现 notebook。✅ 通过。

- `output/view_3d.html`：plotly WebGL 3D 散点（每层一个 z、按 Region 着色；自动按层抽样至 5 万点内）
- `output/reproducible.ipynb`：会话全部步骤的可复现代码 + 结果摘要 + 产物清单（自动导出）

**真实 LLM 验收记录**（2026-09-30，DeepSeek，`MY_LLM_MODEL=deepseek-flash`，配置见项目根目录 `.env`）：

两段式 `--resume` 会话恢复跑完六类指令，全部成功：

1. "加载数据" → load_slices ✅（LLM 主动说明可做插值，符合路由）
2. "查看数据概况" → overview ✅
3. "在两张切片之间插值两层，每层OT迭代200次" → interpolate ×2 ✅（32.6s + 31.1s；LLM 正确使用了 `nb_iter` 参数名）
4. "生成3D可视化" → make_dense + visualize_3d ✅（14,491 spots × 4 层）
5. "验证插值质量" → validate ✅（相邻层 Spearman ρ = 0.996/0.995/0.993；LLM 曾误以为需用户指定层顺序，Planner prompt 已注明自动接线）
6. "导出notebook" → reproducible.ipynb ✅

**真实 LLM 暴露的问题与架构加固**（这正是 Error Reflector 的价值，全部已修）：

1. LLM 编造参数 `ot_iterations` → TypeError → 机械错误 → LLM 修复建议仍错（`ot_iter`）→ **有界重试后诚实失败**。加固：Planner 路由表携带参数名（已不再编造）+ 执行前**参数白名单过滤**（未声明参数直接丢弃并告知）。
2. LLM 向 visualize_3d 传 `'adata': 'dense'` 与自动接线冲突 → TypeError → 修复重试成功。加固：会话注入参数（interpolate 的 adata1/adata2、validate 的 order/names、visualize_3d 的 adata）在 Executor 层安全忽略。

### M2.5 抽稀重建验证（2026-09-30 完成）

**实验**（SpatialZ 论文核心验证 held-out reconstruction）：用同供体间隔切片 151673 与 151675 插值出 alpha=0.5 虚拟中间层，以全程未参与插值的真实 151674 为真值评估。流程与 m2 完全一致（复用 `load_sample / preprocess / make_panel / gen_one`，CPU，nb_iter=1000）。

**数据**：151675 h5ad 取自同一 Zenodo 记录（107.8 MB；当日 Zenodo 单连接限速至 ~18 KB/s，用 `tools/fetch_chunks.py` 16 路并发分块下载提速 10 倍完成）。新增磁盘约 215 MB < 1 GB。

**结果**（`output/holdout_metrics.json`、`output/holdout_comparison.png`）：

| 指标 | 结果 |
|---|---|
| 分层构成平均绝对差 | **1.38 百分点**（各层 0.27–2.96 pp） |
| 逐 Region 平均表达 Spearman | **0.888–0.963**（Layer3 最高 0.963） |
| 逐点 10-NN 预测 Spearman | 中位数 **0.415**（P5–P95：0.300–0.520，σ=0.068） |

解读：组织学结构（分层构成、空间排布）重建精度高（对比图可见虚拟层环带与真值几乎一致）；
逐点表达受 spot 级表达固有噪声限制（log 空间单 spot 信噪比低），中位 ρ≈0.42 属合理水平，
逐 Region 聚合后相关性升至 0.9+，说明表达模式的**空间组织**被准确重建。

**踩坑记录**：

- Zenodo 对单连接限速（同一文件凌晨 1 MB/s、下午 18 KB/s），并发分块是稳定解法，已固化为 `tools/fetch_chunks.py`（可断点续传）
- HVG 面板随插值对变化：151673+151675 并集为 3,482 基因（m2 的 151673+151674 为 3,436），验证时必须切到同一面板再比较

**遗留事项**：

- 无。
- default 合成模式（含 MENDER 微环境度量）未跑通验证，若论文需要对比两模式，需先给 MENDER 路径做同样的 concat 修补。

### M2.6 备用数据下载：STARmap 3D + MERFISH 五切片（2026-09-30 完成）

**背景**：为论文备用实验下载 SpatialZ 论文使用的数据集。

**关键决策**：

- "论文五张切片"实为 **MERFISH 下丘脑数据集**（Moffitt et al., DRYAD，figshare 记录 30418285 有说明）：STARmap 实验本体是一个 3D 体积（教程切成 7 层），不是五张。两个都下载了。
- MERFISH 五切片（`merfish_{4,9,14,19,24}_paste.h5ad`，约 5.5k–5.9k 细胞 × 155 基因）实际来自 **Zenodo 17416727 源码包内 data/ 目录**（M1 时已随源码在 `spatialz_src/`，本轮复制到 `data/merfish_five/` 作为规范数据位置）。
- STARmap 原始数据：官方源 starmapresources.org 本机网络 403，figshare API 亦 403；改用 **GiottoData 镜像**（`drieslab/spatial-datasets`，地址从 GiottoData 的 `inst/extdata/datasets.json` 查出）。

**结果**（`check_backup_data.py` 校验通过）：

- STARmap：28 基因 × 33,598 细胞，z=6–94；753 个零表达细胞过滤后 = **32,845**，与 SpatialZ 论文/教程数字完全一致；已转存 `data/starmap/starmap_3d_raw.h5ad`
- MERFISH 五张全部可读，含 `cell_class` 注释与 `obsm['spatial']`
- `reference/gener_five_merfish.ipynb` 为官方五切片生成 notebook，供复现实验参考

**踩坑记录**：

- `raw.githubusercontent.com` 直连超时，但 `api.github.com` 可通（大文件用 contents API + base64 不行时换 raw 即可，本次 STARmap txt 经 raw 成功）
- STARmap 表达 txt 首行是表头（cell_1..cell_33598），不是无头矩阵；每行 = 基因名 + 各细胞表达

**遗留事项（带注释的 starmap_3d_add_celltype.h5ad）**：

- **已解决访问问题**：用户开启 VPN 后，命令行走本机 Clash 代理 `http://127.0.0.1:7890` 即可访问 figshare（系统直连出口 IP 58.249.x.x 被 figshare IP 级封禁）
- 已从 figshare 记录 30418078 下载官方文件 `data/starmap/STARmap_Wang2018three_data_3D_data.h5ad`（30,475,717 B，md5 `9bb8086c06901b848a553e800e20037b`；与记录 30418285 中同名片逐字节一致）
- **关键发现**：官方发布的即为 32,845 × 28、obs 仅含 `x/y/z/n_counts/leiden` 的基础版；教程用的 `starmap_3d_add_celltype.h5ad`（多 `cell_class/cell_subclass`）是作者本地加注释的衍生文件，**未公开发布**；starmapresources.org 源站已死（首页 403、/data 404）
- **待办**：如需 cell_class，按 marker 基因对 19 个 leiden 聚类做注释重建（Sst→13、Pvalb→7、Vip→16、Npy→18、Flt1→8、Gja1→15、Ctss→12、Mbp→4/17 等，模式已核验），产物须标注"重建版"

### M3.1 interpolate 易用性改造（2026-09-30 完成）

**背景**：interpolate 技能原先强制要求 alpha，用户说"插一层"会被拦；改造为自然用法，默认值写死在技能文档（非 LLM 猜）。

**改动**：

- `skills/interpolate.py`：`alpha=None`（缺省 0.5）；新增 `n_layers=None`（≥1 整数，均匀 alpha 序列 i/(n_layers+1)，循环复用 `gen_one`，与 m2 --all 同思路，未改用 `Generate_multiple_spatialz`——其 alpha 方向约定与 M2 已验证的 `gen_one` 封装不同，循环单层更稳）；alpha 与 n_layers 同给时 alpha 优先并在 `note` 注明；返回 dict 增加 `mode`（"single"/"multi"）区分
- `skills/interpolate.md`：alpha `required: false, default: 0.5`；新增 n_layers input；outputs 按 single/multi 两种结构声明；usage_example 增加 `n_layers=5` 例子
- `agent/executor.py`：interpolate 分支 multi 模式 `session.virtual_slices.extend(...)`（单层仍 append）；输出按 `virtual_alpha{a}` 逐个登记；notebook 模板改 `virtuals = r.get('virtual_slices') or [r['virtual_slice']]`
- `agent/verifier.py`：确认本就不强制 alpha（只查 session/file/数据结构前置条件），无需改动

**验收**（`tests/test_interpolate_ease.py`，端到端走 Executor+Session，17/17 通过）：

- `run(a1, a2)` 不传参 → alpha=0.5 单层 ✓
- `n_layers=3` → alpha 0.25/0.5/0.75 三层，`session.virtual_slices` 长度 3 ✓
- `alpha=2.0` → ValueError 拒绝 ✓
- 附加：alpha+n_layers 同给 → alpha 优先且 note 注明 ✓
- 回归：M3 技能套件、M4 agent 套件全部通过

### M6 后端泛化收尾（2026-09-30 完成）

四项改动，均不破坏现有功能；完成后 DLPFC 两切片全流程回归全部通过。

**M6.1 小基因面板自适应**：`skills/load_slices.py` 重写，接受 N ≥ 2 张切片（file_paths 或 sample_ids，按用户给定顺序排列）；任一切片基因数 < n_hvg 时跳过 HVG 筛选，直接用全基因面板，meta 里注明 `hvg_skipped: true`；N 张切片的基因面板取 var_names 交集保证同一面板。验收：MERFISH（155 基因）不传 n_hvg 也能成功加载（14/14）。

**M6.2 表达合成模式守卫**：面板基因数 > 5000 且 syn_mode="default" 时不再硬拒，返回 `{"mode": "guard", "warning": "...预计需数天，建议 fast；确认请传 confirm_default=true", "path": None}`，不生成任何文件。注意：DLPFC 默认 n_hvg=2000 的 HVG 面板只有 3436 基因 < 5000，不触发守卫；用 n_hvg=8000（面板 11,857 基因）验证拦截路径。已知遗留：default 模式真实执行会触发 MENDER 的 Windows 多进程崩溃（ValueError: need at most 63 handles；M2.5 已记录），不在本次范围。

**M6.3 多切片支持**：
- load_slices 放开"恰好 2 张"限制，返回 `adatas`（list）+ 前两张的 `adata1/adata2` 兼容字段
- 新增技能 `interpolate_multiple`（skills/interpolate_multiple.py/.md）：`run(adata_list, n_layers, ...)`，n_layers 可 int（全部 N-1 对统一）或 list[int]（长度必须 N-1）；每对 alpha = j/(k+1) 升序；slice_id/文件名 `{id1}-{id2}-alpha{a:.3f}`（visualize_3d 的 alpha 正则可直接解析）
- **不用 SpatialZ 的 `Generate_multiple_slices` 的原因**（读源码核实）：它末尾调 `AnnData.concatenate`（anndata 0.13.4 已无此方法），且命名不含 alpha，无法解析；改用逐层调用 `m2_interpolate.gen_one`（已加可选参数 adata1_id/adata2_id/slice_id，缺省保持 M2 行为）
- session.py 加 `slices=[]`、`all_slices`、`has_slices` property（向后兼容 has_pair）；verifier 加 has_slices 分支；executor 加 adata_list 接线
- 验收：5 张 MERFISH 加载 + 每对插 2 层（alpha 0.333/0.667 × 4 对）+ make_dense 13 层 73,919 spots 端到端跑通（9/9）

**M6.4 会话级输出隔离**：`agent_cli.py` 启动时创建 `output/run_YYYYMMDD_HHMMSS/` 传入 session.run_dir；interpolate（out_dir）、make_dense、visualize_3d（out_path）、export_notebook 用户未显式指定路径时默认落 run_dir；resume 会话沿用旧 run_dir。验收：连续两个会话产出两个时间戳目录互不覆盖（8/8）。

**验收汇总**（`tests/test_m6_generalization.py` 新增）：M6.1+6.2 14/14、M6.3 9/9、M6.4 8/8；回归：test_interpolate_ease 17/17、test_m4_agent、test_m3_skills 全部通过。

**已知限制（既有行为，本次未改）**：多对切片时 visualize_3d 的真实切片层序按字母排序而非物理层序（虚拟层夹在中间且按 alpha 单调的两层间行为不受影响）。

## 目录结构

```
毕设/
├── .venv/                  # 项目虚拟环境（不提交版本库）
├── data/                   # DLPFC h5ad 数据（244 MB）+ 备用数据（merfish_five/、starmap/）
├── reference/              # 备用数据参考（gener_five_merfish.ipynb）
├── check_backup_data.py    # M2.6：备用数据完整性校验（一次性脚本）
├── agent/                  # M4 agent 包（llm/planner/verifier/executor/session/registry/exporter）
├── skills/                 # M3 技能注册表（YAML 技能文件 + Python 实现）
├── tests/                  # M3 技能 / M4 agent 单元测试
├── session/                # M4 会话持久化（session.pkl）
├── spatialz_src/           # SpatialZ 原始源码（Zenodo 17416727，未修改）
├── spatialz_code.zip       # SpatialZ 源码压缩包
├── SpatialZ.py             # SpatialZ 主模块（已对 anndata 0.13 做 4 处机械修复）
├── Synthesize.py           # SpatialZ 3D 可视化模块（plotly）
├── agent_cli.py            # M4 终端入口（REPL / 单条指令 / --mock）
├── output/                 # 虚拟切片 h5ad + dense 合并 + 3D HTML + notebook
├── m1_load_data.py         # M1：数据加载 + 概况打印
├── m2_interpolate.py       # M2：SpatialZ 插值（--smoke 烟测 / --alpha 单层 / --all）
├── m2_validate.py          # M2：插值结果验证 + dense 合并
├── SOMENDER-1.1-py3-none-any.whl  # MENDER wheel 备份（重装环境用）
└── README.md               # 本文件：决策与踩坑日志
```
