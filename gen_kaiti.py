"""生成开题报告 Word 文档 v2：全部正文样式，仅用字号/粗体区分层级；字体只用黑体/宋体"""
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

doc = Document()
style = doc.styles["Normal"]
style.font.name = "宋体"
style.font.size = Pt(12)  # 小四
style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

HEI = "黑体"
SONG = "宋体"

def para(text, size=12, bold=False, font=SONG, center=False, indent=True):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold
    run.font.name = font
    run.element.rPr.rFonts.set(qn("w:eastAsia"), font)
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif indent:
        p.paragraph_format.first_line_indent = Pt(24)
    p.paragraph_format.line_spacing = 1.5
    return p

def h1(text):   para(text, size=15, bold=True, font=HEI, indent=False)   # 三号黑体加粗
def h2(text):   para(text, size=13, bold=True, font=HEI, indent=False)   # 四号黑体加粗
def body(text): para(text, size=12, font=SONG)                            # 小四宋体
def plain(text): para(text, size=12, font=SONG, indent=False)

# ===== 标题 =====
para("毕业论文（设计）开题报告", size=16, bold=True, font=HEI, center=True, indent=False)
para("论文（设计）题目：面向肿瘤3D组织重构和解析的智能体系统设计与实现", size=12, bold=True, font=SONG, center=True, indent=False)

# ===== 一 =====
h1("一、国内外研究现状、水平和发展趋势，选题研究的目的和意义")

h2("（一）空间转录组学与三维重建的需求")
body("空间转录组技术（Visium、Xenium、MERFISH、Stereo-seq 等）能够在保留组织空间位置信息的前提下测量基因表达，已成为肿瘤微环境解析、脑科学研究和发育生物学的核心工具。然而，受技术原理与成本限制，现有空间组学实验本质上只能获得稀疏采样的二维切片，相邻切片之间存在大量未经测量的组织间隙，限制了对组织连续三维结构的理解。构建高分辨率的三维细胞图谱，对于解析器官组织原则、肿瘤侵袭的空间机制等科学问题具有重要意义。")

h2("（二）三维重建算法的进展与局限")
body("针对 Z 轴方向稀疏采样的问题，复旦大学冯建峰、原致远团队与中国科学院赵屹团队提出了 SpatialZ 计算框架（Lin et al., Nature Methods, 2026）。该框架基于细胞微环境连续性假设（其思想受单细胞拟时序分析启发），融合最优传输理论与相似度图网络，通过空间对齐、位置生成、细胞状态推断、表达谱推断四个步骤的级联，在真实测量的切片之间生成虚拟切片，从而将稀疏的二维切片堆叠转化为稠密的三维细胞图谱。利用该框架，研究者构建了包含超过 3800 万细胞的小鼠半脑三维图谱，并支持任意角度的虚拟切片与三维空间搜索等新型分析。")
body("然而，此类工具目前以命令行脚本和源代码形式发布，参数链路较长（对齐方法选择、插值密度、细胞属性分配策略、验证指标等），对缺乏计算背景的研究者而言使用门槛较高；同时，其运行缺乏自动化的输入校验与错误处理机制，参数设置不当或数据条件不满足时难以得到清晰的反馈。")

h2("（三）AI 智能体在生物信息分析中的兴起")
body("大语言模型驱动的智能体（agent）正在改变生物信息分析的形态。STAT 等多智能体框架的研究表明：通过持久会话、自描述技能注册表与分阶段管线（任务规划—技能过滤与匹配—前置条件验证—代码执行—错误分类纠错）的架构设计，多智能体系统能够以自然语言对话的方式完成多类空间组学分析任务，其可靠性主要来源于系统架构而非特定大模型本身（Chen et al., bioRxiv, 2026）。针对单细胞与空间组学基础模型的用户评测研究亦指出，当前此类模型的主要瓶颈已从精度转向安装、文档与环境兼容性等可用性问题，而大语言模型辅助的智能体工作流是降低使用门槛的可行路径（Yang et al., National Science Review, 2026）。但现有空间组学智能体的技能库均局限于二维切片分析，尚不支持三维重建流程。")

h2("（四）选题的目的与意义")
body("本课题拟回答的核心问题是：能否将空间转录组三维插值算法（以 SpatialZ 为例）封装为多智能体可调用的标准化技能，使研究者通过自然语言交互完成「切片加载—虚拟切片生成—三维可视化—质量验证」的完整三维重建工作流？")
body("本课题的意义在于：（1）方法层面，将智能体架构延伸至空间组学三维重建场景，是对现有空间分析智能体框架能力的扩展；（2）应用层面，面向肿瘤组织的三维重构与解析场景，降低三维重建工具的使用门槛，使缺乏计算背景的研究者也能开展肿瘤三维空间组学分析；（3）验证层面，在公开连续切片数据集上检验智能体驱动流程的正确性与可靠性，为智能体辅助三维分析提供实证依据。")

# ===== 二 =====
h1("二、研究内容、研究目标及拟解决的关键问题")

h2("（一）研究内容")
body("1. 三维插值算法的技能化封装。将 SpatialZ 的空间对齐、位置生成、细胞状态推断、表达谱推断四个模块封装为自描述技能（声明输入要求、适用数据条件、参数说明与合法范围），接入多智能体技能注册表，使规划器可按数据属性自动匹配调用。")
body("2. 三维场景的会话管理与可视化设计。在持久会话中维护多切片数据对象与三维重建中间结果，实现步骤间的状态传递与依赖自动补全；提供三维交互式可视化输出，支持重建结果的直观检查。")
body("3. 面向三维任务的可靠性机制。针对三维重建特有的失败模式（输入数据缺坐标或注释、参数超出合法范围、基因面板规模与合成模式不匹配等），设计前置条件检查机制与错误分类机制（可机械修复错误的有界自动修复 vs 结构性不可行错误的诚实报告），保证系统失败行为的可控与可解释。")
body("4. 系统验证与评测。在多套公开空间组学数据（人脑背外侧前额叶皮层 Visium 连续切片、小鼠下丘脑 MERFISH 连续切片、STARmap 三维体积数据）上验证系统功能：包括虚拟切片生成结果的合理性检验（细胞类型构成平滑过渡、相邻层表达相关性）、抽稀重建验证（人为隐藏中间层后重建并与真实值对比），以及与文献报道指标的对照。")

h2("（二）研究目标")
body("实现一个面向空间转录组三维插值的多智能体原型系统：用户以自然语言下达指令，系统自动完成任务规划、输入校验、算法执行、错误处理与结果可视化，并输出可复现的分析记录。系统在至少两种不同技术平台（spot 级与单细胞级）的空间组学数据上完成端到端验证，并探索其在肿瘤组织三维解析中的应用。")

h2("（三）拟解决的关键问题")
body("1. 三维重建多步骤链路的参数自动装配与前置条件验证；")
body("2. 虚拟切片数量、基因表达合成模式等关键参数在不同数据条件下的适配策略；")
body("3. 重建质量的自动化评估指标设计及其与文献基准的对照。")

h2("（四）技术路线")
body("用户自然语言指令 → 任务规划器（将指令拆解为有序技能调用计划）→ 技能匹配（按注册表选择三维重建各模块技能）→ 前置条件校验（检查输入数据与参数合法性，缺失信息时向用户澄清）→ 执行器执行 SpatialZ 流程并由错误分类器处理异常（可机械修复错误有界重试，结构性错误诚实报告）→ 三维可视化与可复现分析记录导出。")

# ===== 三 =====
h1("三、研究计划进度")
table = doc.add_table(rows=7, cols=2)
table.style = "Table Grid"
rows = [
    ("时间", "研究内容"),
    ("2026 年 10 月", "完成开题报告；精读 SpatialZ 算法原文与 STAT 智能体框架文献；搭建分析环境，获取并整理公开数据集"),
    ("2026 年 10—11 月", "在公开连续切片数据上复现 SpatialZ 三维插值流程，理解各模块输入输出；完成抽稀重建等验证实验设计"),
    ("2026 年 12 月", "完成 SpatialZ 各模块的技能化封装与智能体系统原型搭建（任务规划、前置校验、执行与错误分类）"),
    ("2027 年 1 月", "中期检查：系统原型演示与阶段性结果汇报"),
    ("2027 年 1—3 月", "完善三维可视化与可靠性机制；在多种数据集上开展系统验证与评测实验"),
    ("2027 年 4—5 月", "撰写毕业论文，完成查重与答辩准备"),
]
for i, (a, b) in enumerate(rows):
    for j, txt in enumerate((a, b)):
        cell = table.rows[i].cells[j]
        cell.text = ""
        run = cell.paragraphs[0].add_run(txt)
        run.font.size = Pt(12)
        run.font.name = SONG
        run.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)
        run.bold = (i == 0)

# ===== 四 =====
h1("四、可行性分析")
body("1. 理论与方法基础：SpatialZ 算法源码与教程文档公开可获取（Zenodo 开源记录），STAT 智能体框架的设计思想已有文献详细阐述，本课题是在二者基础上的集成与扩展，无需从零发明新算法。")
body("2. 数据基础：人脑背外侧前额叶皮层（DLPFC）Visium 连续切片数据（12 张，带人工分层注释，为空间组学标准基准数据）、小鼠下丘脑 MERFISH 连续切片数据、STARmap 三维体积数据均为公开数据，可直接获取；数据规模适中（单数据集 <1 GB），普通个人计算机即可承担计算。")
body("3. 指导与团队基础：导师课题组在空间组学整合算法与智能体方法方向有系统积累，具备指导本课题的研究基础。")
body("4. 个人基础：已修读生物信息学相关课程，完成过 RNA-seq 数据分析流程实践，熟悉 Python 编程与 Linux 服务器操作，具备开展本课题所需的编程与数据分析能力。")

# ===== 五 =====
h1("五、毕业论文提纲（拟）")
plain("第一章  绪论：空间转录组三维重建的挑战与 AI 智能体的机遇")
plain("第二章  相关工作：空间组学三维重建方法；生物信息分析智能体")
plain("第三章  系统设计：多智能体架构、三维插值算法技能化封装、可靠性机制")
plain("第四章  系统实现与验证：数据集、功能验证、抽稀重建实验与结果分析")
plain("第五章  总结与展望")

# ===== 课题来源 =====
h1("六、本课题是否选自教师科研课题")
plain("是。本课题选自国家自然科学基金面上项目：面向3D肿瘤空间域识别与细胞通讯推断的多模态计算方法研究。")

# ===== 六 =====
h1("七、主要参考文献")
refs = [
    "Lin S, Wang Z, Cui Y, et al. Bridging the dimensional gap from planar spatial transcriptomics to 3D cell atlases[J]. Nature Methods, 2026, 23(2): 360-372.",
    "Chen Y, Han S, Chao Z, et al. STAT: a multi-agent framework for integrated and interactive spatial transcriptomics analysis[EB/OL]. bioRxiv, 2026: 2026.05.01.722244.",
    "Yang Q, Zhou Y, Zhang H, et al. A user's roadmap to foundation models on single-cell and spatial-omics[J]. National Science Review, 2026: nwag371.",
    "Maynard K R, Collado-Torres L, Weber L M, et al. Transcriptome-scale spatial gene expression in the human dorsolateral prefrontal cortex[J]. Nature Neuroscience, 2021, 24(3): 425-436.",
    "Moffitt J R, Bambah-Mukku D, Eichhorn S W, et al. Molecular, spatial, and functional single-cell profiling of the hypothalamic preoptic region[J]. Science, 2018, 362(6416): eaau5324.",
    "Wang X, Allen W E, Wright M A, et al. Three-dimensional intact-tissue sequencing of single-cell transcriptional states[J]. Science, 2018, 361(6400): eaat5691.",
    "Zeira R, Land M, Strzalkowski A, et al. Alignment and integration of spatial transcriptomics data[J]. Nature Methods, 2022, 19(5): 567-575.",
    "Wolf F A, Angerer P, Theis F J. SCANPY: large-scale single-cell gene expression data analysis[J]. Genome Biology, 2018, 19: 15.",
]
for i, r in enumerate(refs, 1):
    plain(f"[{i}] {r}")

doc.save(r"C:\Users\ase\Documents\kimi\Workspaces\毕设\开题报告-肿瘤3D重构-终稿.docx")
print("saved")
