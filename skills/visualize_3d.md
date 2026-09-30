---
name: visualize_3d
description: 读取合并的多层切片 AnnData，用 plotly 画 3D 散点图（每层一个 z 坐标、按皮层分层着色）并导出 HTML
inputs:
  - name: adata
    type: AnnData
    required: true
    range: 合并的多层对象；obs['slice_id'] 区分层、obsm['spatial'] 为 x/y 坐标
  - name: out_path
    type: str
    required: false
    default: output/view_3d.html
  - name: color_key
    type: str
    required: false
    default: Region
    range: 必须是 adata.obs 中存在的列（如 Region / layer）
  - name: max_points
    type: int
    required: false
    default: 50000
    range: ">= 1000；超出时按层等比例随机抽样（保持分层结构可见）"
  - name: seed
    type: int
    required: false
    default: 42
preconditions:
  - session.has_dense（缺省时 agent 自动先执行 make_dense 合并多层数据）
  - adata.obs 含 slice_id 列
  - adata.obsm 含 spatial 键
  - adata.obs 含 color_key 指定的着色列
outputs:
  type: dict
  structure:
    path: str      # HTML 文件路径
    n_points: int  # 实际渲染点数
    n_layers: int  # 层数
usage_example: |
  import sys; sys.path.insert(0, "skills")
  import anndata as ad
  from visualize_3d import run
  dense = ad.read_h5ad("output/dlpfc_dense_5sim.h5ad")
  r = run(dense, out_path="output/view_3d.html", color_key="Region")
  print(r["path"], r["n_points"], r["n_layers"])
---

# visualize_3d

用 plotly 把 SpatialZ 重建的多层数据画成 3D 散点图：

- 每个 `slice_id` 映射到一个 z 值（按字典序），x/y 取 `obsm['spatial']`
- 按 `color_key`（默认 `Region` 皮层分层）着色，图例为 "层 | 分层"
- `Scatter3d` 基于 WebGL 渲染；点数超过 `max_points` 时按层等比例抽样保证浏览器流畅
- HTML 自包含（CDN 引入 plotly.js），双击图例可单独显示某一层/分层

实现见 `skills/visualize_3d.py`。
