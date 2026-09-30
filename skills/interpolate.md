---
name: interpolate
description: 在两张已加载切片之间用 SpatialZ 生成虚拟切片：指定 alpha 生成单层，或给 n_layers 均匀插任意多层（含坐标、分层注释与基因表达）
inputs:
  - name: adata1
    type: AnnData
    required: true
    range: 必须是 load_slices 的输出；与 adata2 同一基因面板、dense float32
  - name: adata2
    type: AnnData
    required: true
    range: 必须是 load_slices 的输出；与 adata1 同一基因面板、dense float32
  - name: alpha
    type: float
    required: false
    default: 0.5
    range: "(0, 1) 开区间；越大越靠近 adata1，0.5 为中间层。显式提供时单层生成（优先于 n_layers）"
  - name: n_layers
    type: int
    required: false
    range: ">= 1；均匀插在两张切片之间（alpha = i/(n_layers+1)，i=1..n_layers）。与 alpha 同时提供时 alpha 优先"
  - name: nb_iter
    type: int
    required: false
    default: 1000
    range: ">= 1；CPU 下 1000 次迭代约 106 s/层"
  - name: syn_mode
    type: str
    required: false
    default: fast
    range: "fast（10-NN 同类型均值，适用大基因面板）；default（MENDER 微环境+逐基因抽样，仅适合 <= 数百基因的小面板，Visium 全基因不可用）"
  - name: out_dir
    type: str
    required: false
    default: output/
  - name: confirm_default
    type: bool
    required: false
    default: false
    range: "仅当基因面板 > 5000 且 syn_mode='default' 时需要传 true 确认（预计需数天）；否则忽略"
preconditions:
  - session.has_pair（必须先执行 load_slices）
  - adata1/adata2 为 load_slices 的输出（同一基因面板、dense float32）
outputs:
  type: dict
  structure:
    mode: str             # "single" 或 "multi"
    # single 模式：
    virtual_slice: AnnData # 虚拟切片，obs 含 Region/layer/slice_id/data_type='synthetic'，obsm['spatial'] 为 x/y
    path: str              # 保存的 h5ad 路径 output/virtual_alpha<a>.h5ad
    alpha: float
    note: str | None       # alpha 与 n_layers 同给时的提示
    # multi 模式：
    virtual_slices: list[AnnData]  # 按 alpha 升序
    paths: list[str]               # 与 alphas 对齐
    alphas: list[float]            # 实际使用的 alpha 序列
    n_layers: int
    # 两种模式都有：
    elapsed_sec: float
    # mode="guard"（M6.2：面板 > 5000 基因 + syn_mode='default' 且未确认，未生成数据）：
    warning: str
    n_genes: int
    path: None
usage_example: |
  import sys; sys.path.insert(0, "skills")
  from load_slices import run as load
  from interpolate import run as interp
  pair = load()
  r = interp(pair["adata1"], pair["adata2"], alpha=0.5, nb_iter=1000, syn_mode="fast")
  print(r["path"], r["elapsed_sec"])
  # 均匀插 5 层（alpha = 1/6 .. 5/6）：
  rm = interp(pair["adata1"], pair["adata2"], n_layers=5, nb_iter=1000)
  print(rm["alphas"], rm["paths"])
---

# interpolate

调用 SpatialZ（`Generate_spatialz`，CPU 跑通）在两张真实切片之间生成虚拟切片：

1. 按 alpha 比例从两切片均匀抽样初始化坐标，Sliced Wasserstein 重心梯度下降对齐
2. kNN 多数投票判定每个虚拟 spot 的分层（cell_type_key='Region'）
3. 转移注释属性（layer）
4. 按 `syn_mode` 合成基因表达，结果保存 h5ad

两种模式：**alpha 单层**（alpha 缺省为 0.5）或 **n_layers 多层**（alpha 序列 i/(n_layers+1)，
循环单层逻辑，与 m2 --all 同思路）；两者同时给时 alpha 优先。

注意：`syn_mode='default'` 对 Visium 的 3.3 万基因会退化为上亿次 Python 循环，
**不可用**；本 MVP 一律用 `fast` 模式 + HVG 面板（见 README M2 决策 3）。
M6.2 守卫：面板 > 5000 基因时若选 `default`，技能不生成数据、返回
`{"mode": "guard", "warning": ...}`，用户确认后传 `confirm_default=true` 才执行。
实现见 `skills/interpolate.py`，逻辑复用 `m2_interpolate.gen_one`。
