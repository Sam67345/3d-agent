# 3D-Agent：面向空间转录组三维重建的智能体系统

本科毕业设计项目 —— 《面向肿瘤3D组织重构和解析的智能体系统设计与实现》

用自然语言驱动空间转录组三维重建：对 AI 说"加载这两张切片，中间插一层虚拟切片，画 3D 图"，系统自动完成从数据加载、切片对齐、三维插值到交互式可视化的全流程。

## 这是什么

空间转录组技术测到的是一张张二维组织切片，但真实组织是三维的。**SpatialZ**（Nature Methods 2026）可以在相邻切片之间合成虚拟切片、重建三维结构——但它是一串需要编程操作的脚本。

本项目把这套算法封装成 **AI 智能体可调用的标准化技能**，让不懂编程的研究者用自然语言完成三维重建：

```
用户: "加载 data/merfish_five 下的五张切片，每对之间插 2 层，画 3D 图"
  ↓
Planner（LLM 规划执行步骤）
  ↓
Verifier（执行前校验：参数合法？前置条件满足？）
  ↓
Executor（调用技能：load_slices → interpolate_multiple → visualize_3d）
  ↓
3D 交互式可视化 + 可复现的输出文件
```

## 架构

**三组件智能体**（架构思路参照 STAT，bioRxiv 2026）：

- `agent/planner.py` — 读取技能说明书（`skills/*.md`），让 LLM 输出结构化执行计划
- `agent/verifier.py` — 前置校验与错误分类（可机械修复的错误自动重试，需决策的交还用户）
- `agent/executor.py` — 按计划调用技能，参数名容错映射
- `agent/session.py` — 三维场景会话管理（已加载切片、虚拟层、输出目录），支持保存/恢复

**技能层**（`skills/`，每个技能 = 说明书 .md + 实现 .py）：

| 技能 | 功能 |
|---|---|
| `load_slices` | 加载切片（样本 ID / 文件路径 / 文件夹多切片） |
| `interpolate` | 两切片间生成虚拟切片（SpatialZ 核心流程） |
| `interpolate_multiple` | 多切片逐对插值 |
| `validate` | 结果质量校验 |
| `visualize_3d` | plotly 交互式 3D 可视化 |

**三种使用形态**：

| 形态 | 启动方式 | 说明 |
|---|---|---|
| Gradio 桌面版 | `启动桌面端.bat` | 原型版，保底可用 |
| React 网页版 | `启动React前端.bat` | FastAPI(7861) + React(3001)，浏览器访问 |
| React 桌面版 | `启动React桌面端.bat` | pywebview 独立窗口，演示推荐 |

## 技术栈

- **算法**：SpatialZ（三维插值）/ PASTE（最优传输切片对齐）/ scanpy（预处理）/ MENDER
- **智能体**：Python，LLM 走 OpenAI 兼容接口（默认 DeepSeek，界面内可热切换配置）
- **界面**：React + TypeScript + Vite（前端）/ FastAPI（服务层）/ Gradio（原型）/ pywebview（桌面壳）
- **可视化**：plotly 交互式 3D

## 数据

验证使用公开空间组学数据（均为论文官方发布）：

- **DLPFC**：人背外侧前额叶 Visium 连续切片（Maynard et al., Nat. Neurosci. 2021）
- **MERFISH**：小鼠下丘脑单细胞级连续切片（Moffitt et al., Science 2018）
- **STARmap**：小鼠脑天然三维体积，benchmark 金标准（Wang et al., Science 2018）

数据与输出文件不纳入版本库（见 `.gitignore`），需自行从 GEO/原文补充材料下载。

## 运行

```bat
REM 需要 Python 3.12 + Node.js 20+
REM 1. 创建环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

REM 2. 配置 LLM（或启动后在界面「LLM 设置」里填）
copy .env.example .env   REM 填入 MY_LLM_API_KEY / MY_LLM_BASE_URL / MY_LLM_MODEL

REM 3. 任选一种形态启动
启动React桌面端.bat
```

## 参考文献

1. Lin S, et al. Bridging the dimensional gap from planar spatial transcriptomics to 3D cell atlases. *Nature Methods*, 2026.（SpatialZ）
2. Chen Y, et al. STAT: a multi-agent framework for integrated and interactive spatial transcriptomics analysis. *bioRxiv*, 2026.（架构参照）
3. Zeira R, et al. Alignment and integration of spatial transcriptomics data. *Nature Methods*, 2022.（PASTE 对齐）

## 许可与声明

本科毕业设计项目，开发中。数据版权归原始论文作者所有。
