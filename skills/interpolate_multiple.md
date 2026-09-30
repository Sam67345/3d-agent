---
name: interpolate_multiple
description: 在 N 张已加载切片的每对相邻切片之间，用 SpatialZ 各均匀插入若干层虚拟切片（链式多层插值）
inputs:
  - name: adata_list
    type: list[AnnData]
    required: true
    range: 必须是 load_slices 的输出；N >= 2 张同一基因面板、dense float32，按物理顺序排列；由系统从会话状态注入
  - name: n_layers
    type: int | list[int]
    required: true
    range: "int 时全部 N-1 对统一层数；list 时长度必须等于 N-1，逐对指定；每层 >= 1，alpha = j/(k+1)"
  - name: nb_iter
    type: int
    required: false
    default: 1000
    range: ">= 1；CPU 下 1000 次迭代约 106 s/层"
  - name: syn_mode
    type: str
    required: false
    default: fast
    range: "fast（10-NN 同类型均值）；default（MENDER 微环境+逐基因抽样，仅适合 <= 数千基因的小面板如 MERFISH 155 基因；> 5000 基因需 confirm_default=true 确认）"
  - name: out_dir
    type: str
    required: false
    default: output/
  - name: confirm_default
    type: bool
    required: false
    default: false
    range: "仅当基因面板 > 5000 且 syn_mode='default' 时需要传 true 确认"
preconditions:
  - session.has_slices（必须先执行 load_slices，且加载了 >= 2 张切片）
  - adata_list 为 load_slices 的输出（同一基因面板、dense float32）
outputs:
  type: dict
  structure:
    mode: str              # "multi_pair"；面板 > 5000 基因 + default 未确认时为 "guard"
    virtual_slices: list[AnnData]  # 全部虚拟层（按对顺序、对内 alpha 升序），obs 含 Region/layer/slice_id/data_type='synthetic'
    paths: list[str]       # 各层 h5ad 路径（与 virtual_slices 对齐），命名 {id1}-{id2}-alpha{a}.h5ad
    alphas: list[float]    # 各层 alpha
    pair_ids: list[str]    # 各层所属切片对
    n_layers: int | list[int]
    total_layers: int
    elapsed_sec: float
    # mode="guard" 时：warning / n_genes / path=None
usage_example: |
  import sys; sys.path.insert(0, "skills")
  from load_slices import run as load
  from interpolate_multiple import run as interp_multi
  result = load(file_paths=["data/merfish_five/merfish_4_paste.h5ad",
                            "data/merfish_five/merfish_9_paste.h5ad",
                            "data/merfish_five/merfish_14_paste.h5ad",
                            "data/merfish_five/merfish_19_paste.h5ad",
                            "data/merfish_five/merfish_24_paste.h5ad"])
  r = interp_multi(result["adatas"], n_layers=2, ids=[m["sample_id"] for m in result["meta"]])
  print(r["total_layers"], r["alphas"])
---

# interpolate_multiple

对 N 张切片的每对相邻切片 `(i, i+1)` 生成 `k_i` 层均匀虚拟切片（对应 SpatialZ 源码
`Generate_multiple_slices(adata_list, num_sim_list, ...)` 的语义）：

- 每对内的 alpha 序列为 `j/(k_i+1)`（j=1..k_i，升序；越大越靠近切片 i）
- 逐层调用 M2 已验证的 `gen_one`（坐标对齐 → kNN 分层投票 → 属性转移 → 表达合成）

**为什么不直接调用源码的 `Generate_multiple_slices`**：该函数末尾使用
`AnnData.concatenate`（anndata 0.10 起已移除，0.13.4 确认无此方法），且自行管理
写文件与 `{id1}-{id2}-{j}` 命名（不含 alpha， visualize_3d 的物理层序无法解析）。
本技能按源码的确切语义复现，并输出 visualize_3d 可解析的 `-alpha{a}` 命名。

实现见 `skills/interpolate_multiple.py`。
