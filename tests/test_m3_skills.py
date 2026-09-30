"""M3 技能单元测试：逐个调用 skills/ 下 4 个技能实现。

运行：.venv\\Scripts\\python tests\\test_m3_skills.py
为控制时长，interpolate 用 nb_iter=50（仅验证 API 链路，非生产参数）。
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))

PASSED = []


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    PASSED.append(cond)
    print(f"[{status}] {label} {detail}")


def test_load_slices():
    from load_slices import run
    r = run()
    a1, a2, meta = r["adata1"], r["adata2"], r["meta"]
    check("load_slices: 返回两个 AnnData",
          a1.n_obs > 3000 and a2.n_obs > 3000, f"({a1.n_obs}, {a2.n_obs} spots)")
    check("load_slices: 同一基因面板", list(a1.var_names) == list(a2.var_names),
          f"{a1.n_vars} genes")
    check("load_slices: dense float32",
          hasattr(a1.X, "shape") and not hasattr(a1.X, "todense")
          and str(a1.X.dtype) == "float32", f"dtype={a1.X.dtype}")
    check("load_slices: 含 Region 与 spatial",
          "Region" in a1.obs and "spatial" in a1.obsm)
    check("load_slices: meta 含坐标范围",
          all("x_range" in m and "n_spots" in m for m in meta))
    check("load_slices: 非法参数报错",
          _raises(lambda: run(sample_ids=["151673"])))
    return a1, a2


def test_interpolate(a1, a2):
    from interpolate import run
    with tempfile.TemporaryDirectory() as td:
        r = run(a1, a2, alpha=0.5, nb_iter=50, out_dir=td)
        sim = r["virtual_slice"]
        check("interpolate: 返回虚拟切片", sim.n_obs > 3000, f"{sim.n_obs} spots")
        check("interpolate: obs 标记 synthetic",
              sim.obs["data_type"].iloc[0] == "synthetic")
        check("interpolate: 含 Region 注释", sim.obs["Region"].notna().all())
        check("interpolate: 文件已保存", Path(r["path"]).exists(), r["path"])
        check("interpolate: 非法 alpha 报错", _raises(lambda: run(a1, a2, alpha=1.5)))
        check("interpolate: 非法 syn_mode 报错",
              _raises(lambda: run(a1, a2, alpha=0.5, syn_mode="x")))
        return sim


def test_validate(a1, a2, sim):
    from validate import run
    order = [a2, sim, a1]
    names = ["151674", "virtual_a0.5", "151673"]
    r = run(order, names)
    check("validate: 三项检查齐全",
          set(r) == {"layer_fractions", "adjacent_spearman", "coord_ranges"})
    rho_mid = r["adjacent_spearman"][0]["rho"]
    check("validate: 相邻层 Spearman 正常", 0.9 < rho_mid <= 1.0, f"rho={rho_mid:.4f}")
    fr = r["layer_fractions"]
    check("validate: 分层构成含 7 类", len(fr) == 7, ",".join(sorted(fr)))
    check("validate: 坐标范围结构完整",
          all("x_range" in v for v in r["coord_ranges"].values()))
    check("validate: 少于 3 层报错", _raises(lambda: run(order[:2], names[:2])))


def test_visualize(a1, a2, sim):
    from visualize_3d import run
    import anndata as ad
    for a, sid, dt in ((a1, "151673", "real"), (a2, "151674", "real")):
        a.obs["slice_id"] = sid
        a.obs["data_type"] = dt
    dense = ad.concat([a1, a2, sim], join="outer", merge="same")
    dense.obs["Region"] = dense.obs["Region"].astype(str)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "view_3d.html"
        r = run(dense, out_path=out, max_points=20000)
        check("visualize_3d: HTML 已生成", out.exists() and out.stat().st_size > 10000,
              f"{out.stat().st_size/1024:.0f} KB")
        check("visualize_3d: 默认输出路径逻辑", r["n_layers"] == 3, f"{r['n_layers']} 层")
        check("visualize_3d: 抽样生效", r["n_points"] <= 20000, f"{r['n_points']} points")
        bad = sim.copy()
        del bad.obs["slice_id"]
        check("visualize_3d: 缺 slice_id 报错",
              _raises(lambda: run(bad)))


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def main():
    print("== test_load_slices ==")
    a1, a2 = test_load_slices()
    print("\n== test_interpolate (nb_iter=50) ==")
    sim = test_interpolate(a1, a2)
    print("\n== test_validate ==")
    test_validate(a1, a2, sim)
    print("\n== test_visualize_3d ==")
    test_visualize(a1, a2, sim)

    print(f"\n{'='*40}\n共 {len(PASSED)} 项, 通过 {sum(PASSED)} 项, "
          f"失败 {len(PASSED)-sum(PASSED)} 项")
    sys.exit(0 if all(PASSED) else 1)


if __name__ == "__main__":
    main()
