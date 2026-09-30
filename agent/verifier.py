"""Verifier：执行前逐项检查技能 YAML 声明的 preconditions。

可靠性核心：前置条件不满足时**向用户提问澄清，禁止编造**。返回 (ok, missing) —
CLI 层负责把 missing 清单呈现给用户。
"""

from pathlib import Path

from .registry import ROOT


def _check(cond, session, args, skill):
    """单条前置条件求值。cond 为 YAML preconditions 中的字符串。"""
    # Session 状态类（YAML 里可能带中文注释后缀，用前缀匹配）
    if cond.startswith("session.has_pair"):
        return session.has_pair or (False, "尚未加载切片对（需先执行 load_slices）")
    if cond.startswith("session.has_virtual"):
        return session.has_virtual or (False, "尚无虚拟切片（需先执行 interpolate）")
    if cond.startswith("session.has_slices"):
        return session.has_slices or (False, "尚未加载 >= 2 张切片（需先执行 load_slices）")
    if cond.startswith("session.has_dense"):
        return session.has_dense or (False, "尚未合并多层数据（需先执行 make_dense）")
    if cond.startswith("len(session.logs)"):
        return len(session.logs) > 0 or (False, "会话还没有执行过任何步骤")

    # 文件存在类：优先检查用户直接给出的 file_paths（绝对/相对路径），
    # 否则按约定命名检查 data/<id>_filtered_feature_bc_matrix.h5ad
    if cond.startswith("data/<sample_id>") or "folder > file_paths" in cond:
        fps = args.get("file_paths")
        if args.get("folder"):  # M7.1：folder 优先级最高
            d = Path(args["folder"])
            if not d.exists():
                return False, f"folder 目录不存在: {args['folder']}"
            if not d.is_dir():
                return False, f"folder 必须是目录（不是文件）: {args['folder']}"
            n = len(list(d.glob("*.h5ad")))
            return n >= 2, f"folder 下的 h5ad 少于 2 个（实际 {n} 个）" if n < 2 else None
        if fps:
            if isinstance(fps, str):
                fps = [f.strip() for f in fps.split(",")]
            bad = [p for p in fps
                   if not Path(p).exists() or Path(p).suffix != ".h5ad"]
            return (not bad,
                    f"file_paths 无效（不存在或非 .h5ad）: {bad}" if bad else None)
        ids = args.get("sample_ids") or ["151673", "151674"]
        if isinstance(ids, str):
            ids = [i.strip() for i in ids.split(",")]
        missing = [sid for sid in ids
                   if not (ROOT / "data" / f"{sid}_filtered_feature_bc_matrix.h5ad").exists()]
        return not missing, f"缺少切片文件: {missing}" if missing else None

    # 数据结构类（由 session 中已加载数据满足；load_slices 自身不适用——
    # 它加载的文件内部结构在执行期校验，缺列会以 KeyError 走机械修复路径）
    for key, label in (("obsm['spatial']", "空间坐标 obsm['spatial']"),
                       ("obs['Region']", "分层注释 obs['Region']"),
                       ("obs['slice_id']", "层标识 obs['slice_id']")):
        if key in cond:
            if skill == "load_slices":
                return True, None
            if not session.has_pair:
                return False, "尚未加载切片（需先执行 load_slices）"
            return True, None

    # 默认：未知条件视为通过（由技能实现内部再校验）
    return True, None


def verify(skill_name, meta, session, args):
    """逐项检查 preconditions。

    Returns
    -------
    (ok, problems): problems 为不满足项的可读清单（空 = 全部满足）
    """
    problems = []
    for cond in meta.get("preconditions", []) or []:
        result = _check(cond, session, args, skill_name)
        if isinstance(result, tuple):
            ok, extra = result
        else:
            ok, extra = result, None
        if not ok:
            problems.append(extra or cond)
    return not problems, problems
