"""技能实现：visualize_3d —— plotly 3D 散点可视化，导出 HTML。

每层切片一个 z 坐标，按 Region（皮层分层）着色。
层序按物理位置排列：真实切片（data_type='real'）排两端，虚拟切片按 alpha
从大到小排中间（alpha 从 obs 或 slice_id 正则提取）；无 data_type 回退字母序并警告。
plotly Scatter3d 本身即 WebGL 渲染；超大数据量时按层等比例抽样保证流畅。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objs as go

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m2_interpolate import OUT_DIR  # noqa: E402

DEFAULT_OUT = OUT_DIR / "view_3d.html"


def run(adata, out_path=None, color_key="Region", max_points=50000, seed=42):
    """把多层切片画成 3D 散点图并导出 HTML。

    Parameters
    ----------
    adata : AnnData
        含多张切片的合并对象，obs['slice_id'] 区分层，obsm['spatial'] 为 x/y，
        z 由 slice_id 的顺序位置生成。
    out_path : str | Path | None
        HTML 输出路径，默认 output/view_3d.html。
    color_key : str
        着色列（默认 obs['Region'] 皮层分层）。
    max_points : int
        图上最多渲染的 spot 数，超出按层等比例随机抽样。
    seed : int
        抽样随机种子（可复现）。

    Returns
    -------
    dict:
        path : str    HTML 文件路径
        n_points : int  实际渲染点数
        n_layers : int  层数
    """
    if "slice_id" not in adata.obs:
        raise KeyError("缺少 obs['slice_id']，无法区分切片层")
    if "spatial" not in adata.obsm:
        raise KeyError("缺少 obsm['spatial'] 坐标")
    if color_key not in adata.obs:
        raise KeyError(f"缺少 obs['{color_key}'] 着色列")

    df = pd.DataFrame({
        "x": adata.obsm["spatial"][:, 0],
        "y": adata.obsm["spatial"][:, 1],
        "slice": adata.obs["slice_id"].astype(str).values,
        "color": adata.obs[color_key].astype(str).values,
    })

    # 抽样（保持各层比例）
    if len(df) > max_points:
        rng = np.random.default_rng(seed)
        df = df.sample(n=max_points, random_state=rng).reset_index(drop=True)

    # 层 → z：按物理层序排列，而非字母序——
    # 真实切片（data_type=='real'）按 slice_id 排序后排两端（最小值在底），
    # 虚拟切片按 alpha 从大到小（alpha 越大越靠近 adata1）排在中间；
    # alpha 优先取 obs['alpha']，否则从 slice_id（virtual_alpha0.833）正则提取。
    # 无 data_type 列时回退字母序并警告。
    def _alpha_of(slice_id):
        sid = str(slice_id)
        if "alpha" in adata.obs:
            vals = adata.obs.loc[adata.obs["slice_id"].astype(str) == sid, "alpha"]
            if len(vals) and pd.notna(vals.iloc[0]):
                return float(vals.iloc[0])
        import re
        m = re.search(r"alpha(\d+(?:\.\d+)?)", sid)
        return float(m.group(1)) if m else None

    all_layers = list(dict.fromkeys(df["slice"].tolist()))
    if "data_type" in adata.obs:
        dtypes = adata.obs.drop_duplicates("slice_id").set_index(
            adata.obs.drop_duplicates("slice_id")["slice_id"].astype(str))["data_type"]
        reals = sorted([s for s in all_layers if str(dtypes.get(s, "")) == "real"])
        synths = [s for s in all_layers if str(dtypes.get(s, "")) == "synthetic"]
        synths = sorted(synths, key=lambda s: (_alpha_of(s) is None, -(_alpha_of(s) or 0)))
        layers = ([reals[0]] if reals else []) + synths + reals[1:]
    else:
        print("⚠️  visualize_3d: 缺少 obs['data_type']，层序回退为 slice_id 字母排序")
        layers = sorted(all_layers)
    z_of = {s: float(i) for i, s in enumerate(layers)}
    df["z"] = df["slice"].map(z_of)

    # plotly 的 Scatter3d 本身就基于 WebGL 渲染；超大数据量时额外做抽样保流畅
    scatter = go.Scatter3d
    mode = "markers"

    traces = []
    # 按物理层序 × 分层 分组作图，图例顺序与 z 轴一致
    for sl in layers:
        for cl, g in df[(df["slice"] == sl)].groupby("color", sort=True):
            traces.append(scatter(
                x=g["x"], y=g["y"], z=g["z"],
                mode=mode,
                marker=dict(size=3, opacity=0.85),
                name=f"{sl} | {cl}",
                legendgroup=cl,
            ))

    fig = go.Figure(traces)
    fig.update_layout(
        title=f"SpatialZ 3D 重建（{len(layers)} 层, 按 {color_key} 着色）",
        scene=dict(xaxis_title="x", yaxis_title="y", zaxis_title="slice (z)"),
        width=1100, height=800,
    )

    out = Path(out_path) if out_path else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out, include_plotlyjs="cdn")

    return {"path": str(out), "n_points": int(len(df)), "n_layers": len(layers),
            "layer_order": layers}
