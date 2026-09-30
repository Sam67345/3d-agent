---
name: load_slices
description: 载入 N 张（>= 2）h5ad 切片并完成归一化与公共基因面板预处理，输出可直接插值的切片列表（小基因面板自动跳过 HVG）
inputs:
  - name: folder
    type: str
    required: false
    default: null
    range: "给一个文件夹路径，自动加载其中全部 .h5ad，按文件名排序（自然序：4 < 9 < 14 < 19 < 24）；文件夹内需 >= 2 个 h5ad；优先级高于 file_paths 与 sample_ids"
  - name: file_paths
    type: list[str]
    required: false
    default: null
    range: ">= 2 个 h5ad 文件的绝对或相对路径，按给定顺序加载；提供时优先于 sample_ids，适用于任意命名的数据（如 MERFISH）"
  - name: sample_ids
    type: list[str]
    required: false
    default: ["151673", "151674"]
    range: ">= 2 个元素；每个 ID 对应 data/<id>_filtered_feature_bc_matrix.h5ad 必须存在"
  - name: n_hvg
    type: int
    required: false
    default: 2000
    range: ">= 100；N 切片 HVG 取并集作为公共基因面板；任一切片基因数 < n_hvg 时跳过 HVG 筛选，直接用全基因面板（交集），meta 中 hvg_skipped=true"
  - name: data_dir
    type: str
    required: false
    default: data/
    range: 目录必须存在且含各切片的 h5ad 文件
preconditions:
  - 输入来源优先级 folder > file_paths > sample_ids：提供 folder 时要求目录存在且含 >= 2 个 h5ad；提供 file_paths 时要求这些路径存在且为 .h5ad；否则要求 data/<sample_id>_filtered_feature_bc_matrix.h5ad 存在
  - 每个 h5ad 含 obsm['spatial'] 空间坐标
  - 每个 h5ad 含分层注释列：obs['Region']（DLPFC）或 obs['cell_class']（MERFISH），缺失值会在加载时丢弃
outputs:
  type: dict
  structure:
    adatas: list[AnnData] # 按输入顺序的 N 张切片：log1p 归一化、同一基因面板、dense float32
    adata1: AnnData       # 仅 N == 2 时提供（向后兼容两切片流程）
    adata2: AnnData       # 仅 N == 2 时提供
    meta: list[dict]      # 每切片 {sample_id, n_spots, n_genes, x_range, y_range, hvg_skipped}
usage_example: |
  import sys; sys.path.insert(0, "skills")
  from load_slices import run
  result = run(sample_ids=["151673", "151674"], n_hvg=2000)
  # 或整个文件夹（自动加载全部 h5ad，按文件名自然序——推荐用于 MERFISH 五张）：
  # result = run(folder="data/merfish_five")
  # 或直接用文件路径（任意命名的数据）：
  # result = run(file_paths=["data/merfish_five/merfish_4_paste.h5ad",
  #                          "data/merfish_five/merfish_9_paste.h5ad",
  #                          "data/merfish_five/merfish_14_paste.h5ad",
  #                          "data/merfish_five/merfish_19_paste.h5ad",
  #                          "data/merfish_five/merfish_24_paste.h5ad"])
  adatas = result["adatas"]           # N > 2 时用 adatas；N == 2 时还有 adata1/adata2
  print(result["meta"])
---

# load_slices

载入 N 张（>= 2）相邻切片（空间转录组，h5ad 格式），按用户给定顺序排列，执行：

1. 读取 h5ad，丢弃无分层注释的边缘点（`obs['Region']` 为 NaN；MERFISH 用 `obs['cell_class']`，自动复制为 `Region`）
2. 文库大小归一化（`sc.pp.normalize_total`，target_sum=1e4）+ log1p
3. 基因面板：每切片选 top `n_hvg` 高变基因（seurat flavor），N 切片取**并集**为公共面板；
   任一切片基因数 < `n_hvg` 时**跳过 HVG 筛选**，直接用全基因面板（N 切片交集），
   返回的 meta 里每张切片标 `hvg_skipped: true`（M6.1 小面板自适应）
4. 表达矩阵转 dense float32

## 三种输入方式（优先级：folder > file_paths > sample_ids）

| 方式 | 参数 | 适用场景 |
| --- | --- | --- |
| 文件夹 | `folder="data/merfish_five"` | 一整个文件夹的数据，**自动加载全部 .h5ad**，按文件名自然序排列（推荐） |
| 文件路径 | `file_paths=[...]` | 需要显式指定顺序或挑其中几张 |
| 切片 ID | `sample_ids=["151673","151674"]` | 符合约定命名的 DLPFC 数据（`data/<id>_filtered_feature_bc_matrix.h5ad`） |

> 文件名排序用自然序而不是纯字符串序：`merfish_4 → 9 → 14 → 19 → 24`，
> 而不是字符串序的 `14 → 19 → 24 → 4 → 9`（后者会把物理层序搞反）。

返回的切片列表可直接传给 `interpolate`（用前两张）或 `interpolate_multiple`（全部 N 张）。
N > 2 时返回值只有 `{"adatas", "meta"}`；N == 2 时额外保留 `adata1/adata2` 向后兼容。
实现见 `skills/load_slices.py`，逻辑复用 `m1_load_data.load_sample` 与 `m2_interpolate` 的预处理规则。
