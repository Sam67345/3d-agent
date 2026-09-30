---
name: validate
description: 对真实+虚拟切片序列执行三项质量检查（分层构成过渡、相邻层表达相关性、坐标范围），返回结构化结果
inputs:
  - name: order
    type: list[AnnData]
    required: true
    range: ">= 3 个元素；按 z 轴顺序排列（如 [切片A, 虚拟1, ..., 切片B]），表达矩阵基因面板必须一致"
  - name: names
    type: list[str]
    required: true
    range: 长度与 order 一致
preconditions:
  - session.has_pair（必须先执行 load_slices）
  - session.has_virtual（至少一层虚拟切片）
  - 每个 AnnData 含 obs['Region'] 分层注释列
  - 每个 AnnData 含 obsm['spatial'] 空间坐标
outputs:
  type: dict
  structure:
    layer_fractions: dict[str, dict[str, float]] # 验证1：分层 -> 切片名 -> 构成比例，应随 alpha 平滑过渡
    adjacent_spearman: list[dict]                # 验证2：[{pair: [name_i, name_j], rho, pvalue}, ...]
    coord_ranges: dict[str, dict]                # 验证3：切片名 -> {x_range, y_range, n_spots}
usage_example: |
  import sys; sys.path.insert(0, "skills")
  import anndata as ad
  from load_slices import run as load
  from interpolate import run as interp
  from validate import run as validate
  pair = load()
  sim = interp(pair["adata1"], pair["adata2"], alpha=0.5, nb_iter=50)["virtual_slice"]
  order = [pair["adata2"], sim, pair["adata1"]]
  names = ["151674", "virtual_a0.5", "151673"]
  result = validate(order, names)
  print(result["adjacent_spearman"])
---

# validate

三项检查（与 README M2 验证一致）：

1. **分层构成过渡**：各 Layer1–6/WM 比例应随 alpha 从切片 2 的构成平滑过渡到切片 1
2. **相邻层表达一致性**：相邻切片全基因平均表达 Spearman 相关（MVP 验收要求 rho 高，M2 实测 >= 0.994）
3. **坐标范围**：虚拟切片坐标应落在两张真实切片之间

实现见 `skills/validate.py`，逻辑复用 `m2_validate.run_validation`。
