"""Executor + Error Reflector：执行技能调用，错误分类 + 有界重试。

可靠性原则（交接包核心）：
- 可机械修复的错误（路径、缺列、参数越界）→ 反馈给 LLM 建议修复，自动重试一次
- 结构性不可行（如基因不在面板、环境缺依赖）→ 诚实报告，禁止无限重试
"""

import difflib
import importlib
import json
import sys
import traceback
from pathlib import Path

from .registry import ROOT, SKILLS_DIR, load_registry
from .session import StepLog, summary

sys.path.insert(0, str(SKILLS_DIR))

MECHANICAL = (FileNotFoundError, KeyError, ValueError, TypeError)
MAX_RETRY = 1  # 有界重试：机械错误最多自动修一次

# LLM 常编造的常见参数名 → 技能文档声明的真实参数名
PARAM_ALIASES = {
    "slice_ids": "sample_ids",      # 实际报错过的案例
    "slice_id": "sample_ids",
    "sample_names": "sample_ids",
    "paths": "file_paths",
    "path": "file_paths",
    "h5ad_paths": "file_paths",
    "folder_path": "folder",
    "dir": "folder",
    "data_folder": "folder",
    "directory": "folder",
    "n_interp": "n_layers",
    "num_layers": "n_layers",
    "layers": "n_layers",
}


def declared_inputs(skill, registry=None):
    """技能 md 中声明的参数名集合。"""
    reg = registry or load_registry()
    meta = (reg or {}).get(skill) or {}
    return {i["name"] for i in (meta.get("inputs") or []) if i.get("name")}


def normalize_args(skill, args, registry=None, emit=None):
    """参数名容错：LLM 编造的未声明参数先映射到最接近的已声明参数。

    顺序：已声明 → PARAM_ALIASES → difflib 模糊匹配 -> 丢弃（并给出提示）。
    返回 (修复后的 args, 说明列表)。
    """
    allowed = declared_inputs(skill, registry)
    if not allowed:            # 内置技能等无声明的来源：原样透传
        return dict(args), []
    fixed, notes = {}, []
    for k, v in args.items():
        if k in allowed:
            fixed[k] = v
            continue
        target = PARAM_ALIASES.get(k)
        if target is None or target not in allowed:
            close = difflib.get_close_matches(k, sorted(allowed), n=1, cutoff=0.62)
            target = close[0] if close else None
        if target:
            notes.append(f"{k} → {target}")
            fixed.setdefault(target, v)
        else:
            notes.append(f"丢弃未声明参数 {k}")
    if emit and notes:
        emit(f"  [normalize] {skill}: " + ", ".join(notes))
    return fixed, notes


def _code_for(skill, args, session):
    """生成可复现的 notebook 代码文本。"""
    if skill == "load_slices":
        a = dict(args)
        return (f"import sys; sys.path.insert(0, 'skills')\n"
                f"from load_slices import run as load_slices\n"
                f"pair = load_slices.run({json.dumps(a, ensure_ascii=False)})\n"
                f"adatas = pair['adatas']\n"
                f"a1, a2 = adatas[0], adatas[1]")
    if skill == "interpolate":
        a = {k: v for k, v in args.items() if k not in ("adata1", "adata2")}
        return (f"from interpolate import run as interpolate\n"
                f"r = interpolate(a1, a2, {json.dumps(a, ensure_ascii=False)})\n"
                f"virtuals = r.get('virtual_slices') or [r['virtual_slice']]")
    if skill == "interpolate_multiple":
        a = {k: v for k, v in args.items() if k not in ("adata_list",)}
        return (f"from interpolate_multiple import run as interpolate_multiple\n"
                f"r = interpolate_multiple(adatas, {json.dumps(a, ensure_ascii=False)})\n"
                f"virtuals = r.get('virtual_slices', [])")
    if skill == "validate":
        meta = getattr(session, "pair_meta", None) or []
        id1 = meta[0]["sample_id"] if len(meta) > 0 else "slice1"
        id2 = meta[1]["sample_id"] if len(meta) > 1 else "slice2"
        return ("from validate import run as validate\n"
                "order = [a2] + session_virtuals + [a1]\n"
                f"names = ['{id2}'] + [v.obs['slice_id'][0] for v in session_virtuals] + ['{id1}']\n"
                "vres = validate(order, names)")
    if skill == "visualize_3d":
        return ("from visualize_3d import run as visualize_3d\n"
                "vr = visualize_3d(dense, out_path='output/view_3d.html')")
    return f"# builtin step: {skill}({json.dumps(args, ensure_ascii=False)})"


class Executor:
    def __init__(self, session, llm=None, verbose=True):
        self.session = session
        self.llm = llm
        self.verbose = verbose
        self._registry = None   # 惰性加载：只在需要参数校验时读 skills/*.md

    def _emit(self, msg):
        if self.verbose:
            print(msg)

    @property
    def registry(self):
        if self._registry is None:
            self._registry = load_registry()
        return self._registry

    # ---- 各技能的实际调用（含 session 接线） ----
    # 由会话自动注入的数据对象参数：LLM 即使传入也会被安全忽略
    WIRED_ARGS = {
        "interpolate": ("adata1", "adata2"),
        "interpolate_multiple": ("adata_list",),
        "validate": ("order", "names"),
        "visualize_3d": ("adata",),
    }

    def _invoke(self, skill, args):
        session = self.session
        # M7.1：调用前做参数名容错（LLM 编造的参数先映射再说）
        args, _notes = normalize_args(skill, args, self._registry, emit=self._emit)
        for k in self.WIRED_ARGS.get(skill, ()):
            if k in args:
                self._emit(f"  [wire] 忽略会话注入参数 {k}={args.pop(k)!r}")
        if skill == "load_slices":
            from load_slices import run
            r = run(**args)
            adatas = list(r["adatas"])
            session.slices = adatas       # M6.3：全部 N 张（按加载顺序）
            session.adatas = adatas       # M7.1：同名别称，供多切片下游读取
            # 前两张仍写入 pair（向后兼容；N > 2 时 r 不再含 adata1/adata2）
            session.pair = (adatas[0], adatas[1]) if len(adatas) >= 2 else None
            session.pair_meta = r["meta"]
            return {"meta": r["meta"], "n_slices": len(adatas)}

        if skill == "interpolate":
            from interpolate import run
            if not session.has_pair:
                raise RuntimeError("interpolate 需要 session 中已加载的切片对")
            a1, a2 = session.pair
            a = dict(args)
            if getattr(session, "run_dir", None) and "out_dir" not in a:
                a["out_dir"] = str(session.run_dir)
            r = run(a1, a2, **a)
            if r["mode"] == "guard":
                return {"warning": r["warning"]}
            if r["mode"] == "multi":
                session.virtual_slices.extend(r["virtual_slices"])
                for p, a_ in zip(r["paths"], r["alphas"]):
                    session.outputs[f"virtual_alpha{a_}"] = p
                return {"paths": r["paths"], "alphas": r["alphas"],
                        "elapsed_sec": r["elapsed_sec"],
                        "n_layers": r["n_layers"]}
            session.virtual_slices.append(r["virtual_slice"])
            session.outputs[f"virtual_alpha{r['alpha']}"] = r["path"]
            result = {"path": r["path"], "elapsed_sec": r["elapsed_sec"],
                      "n_spots": r["virtual_slice"].n_obs}
            if r.get("note"):
                result["note"] = r["note"]
            return result

        if skill == "interpolate_multiple":
            from interpolate_multiple import run
            if not session.has_slices:
                raise RuntimeError("interpolate_multiple 需要 session 中已加载的 >= 2 张切片")
            slices = session.all_slices
            ids = [m.get("sample_id") for m in session.pair_meta] \
                if session.pair_meta else None
            a = dict(args)
            if getattr(session, "run_dir", None) and "out_dir" not in a:
                a["out_dir"] = str(session.run_dir)
            r = run(slices, ids=ids, **a)
            if r["mode"] == "guard":
                return {"warning": r["warning"]}
            session.virtual_slices.extend(r["virtual_slices"])
            for p in r["paths"]:
                session.outputs[Path(p).stem] = p
            return {"paths": r["paths"], "alphas": r["alphas"],
                    "total_layers": r["total_layers"],
                    "elapsed_sec": r["elapsed_sec"]}

        if skill == "make_dense":
            return self._make_dense()

        if skill == "validate":
            from validate import run
            if not session.has_pair:
                raise RuntimeError("validate 需要已加载的切片对")
            if not session.has_dense:
                self._make_dense()
            a1, a2 = session.pair
            id1, id2 = self._pair_ids()
            order = [a2] + session.virtual_slices + [a1]
            names = ([id2] + [f"virtual{i+1}" for i in range(len(session.virtual_slices))]
                     + [id1])
            r = run(order, names)
            rhos = [round(x["rho"], 4) for x in r["adjacent_spearman"]]
            return {"adjacent_spearman_rho": rhos, "detail": summary(r)}

        if skill == "visualize_3d":
            from visualize_3d import run
            if not session.has_dense:
                self._make_dense()
            a = dict(args)
            if getattr(session, "run_dir", None) and "out_path" not in a:
                a["out_path"] = str(Path(session.run_dir) / "view_3d.html")
            r = run(session.dense, **a)
            session.outputs["view_3d"] = r["path"]
            return r

        if skill == "overview":
            if not session.has_pair:
                raise RuntimeError("overview 需要已加载的切片对")
            out = []
            for m in (session.pair_meta or []):
                out.append(m)
            out.append({"virtual_slices": len(session.virtual_slices),
                        "has_dense": session.has_dense,
                        "outputs": session.outputs})
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return {"printed": True}

        if skill == "export_notebook":
            from .exporter import export_notebook
            a = dict(args)
            if getattr(session, "run_dir", None) and "out_path" not in a:
                a["out_path"] = str(Path(session.run_dir) / "reproducible.ipynb")
            path = export_notebook(session, **a)
            session.outputs["notebook"] = path
            return {"path": path}

        raise ValueError(f"未知技能: {skill}")

    def _pair_ids(self):
        """从 session.pair_meta 取两张真实切片的实际名称（不再硬编码）。"""
        meta = self.session.pair_meta or []
        id1 = meta[0]["sample_id"] if len(meta) > 0 else "slice1"
        id2 = meta[1]["sample_id"] if len(meta) > 1 else "slice2"
        return id1, id2

    def _make_dense(self):
        import anndata as ad
        session = self.session
        # M6.3：多切片——合并全部真实切片（保持加载顺序）+ 全部虚拟切片
        sources = session.all_slices
        ids = [m.get("sample_id", f"slice{i+1}") for i, m in
               enumerate(session.pair_meta or [])]
        while len(ids) < len(sources):
            ids.append(f"slice{len(ids) + 1}")
        reals = []
        for a, sid in zip(sources, ids):
            a = a.copy()
            a.obs["slice_id"] = sid
            a.obs["data_type"] = "real"
            reals.append(a)
        dense = ad.concat(reals + session.virtual_slices,
                          join="outer", merge="same")
        dense.obs["Region"] = dense.obs["Region"].astype(str)
        dense.obs["layer"] = dense.obs["Region"]
        session.dense = dense
        out = (Path(getattr(session, "run_dir", None)) / "dlpfc_dense_agent.h5ad") \
            if getattr(session, "run_dir", None) else ROOT / "output" / "dlpfc_dense_agent.h5ad"
        out.parent.mkdir(parents=True, exist_ok=True)
        dense.write_h5ad(out)
        session.outputs["dense"] = str(out)
        self._emit(f"  [make_dense] {dense.n_obs} spots x {dense.n_vars} genes -> {out}")
        return {"n_obs": dense.n_obs, "n_vars": dense.n_vars}

    # ---- 错误分类 ----
    @staticmethod
    def classify(err):
        if isinstance(err, MECHANICAL):
            return "mechanical"
        return "structural"

    def _ask_fix(self, skill, args, err):
        """Error Reflector：把错误喂回 LLM 要修复后的参数。LLM 不可用时返回 None。"""
        if self.llm is None:
            return None
        try:
            prompt = (
                f"技能 {skill} 以参数 {json.dumps(args, ensure_ascii=False)} 调用时报错：\n"
                f"{type(err).__name__}: {err}\n"
                f"请只输出修复后的完整参数 JSON：{{\"args\": {{...}}}}")
            raw = self.llm.chat([{"role": "user", "content": prompt}], temperature=0)
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            return data.get("args")
        except Exception:
            return None

    def run_step(self, skill, args):
        """执行一步，带错误分类与有界重试。返回 (ok, message)。"""
        code = _code_for(skill, args, self.session)
        try:
            result = self._invoke(skill, args)
            self.session.add_log(StepLog(skill, summary(args), code,
                                         summary(result), ok=True))
            return True, summary(result)

        except Exception as err:  # noqa: BLE001 - 需要分类所有错误
            kind = self.classify(err)
            detail = f"{type(err).__name__}: {err}"
            self._emit(f"  [error:{kind}] {detail}")

            fixed_args = None
            if kind == "mechanical":
                fixed_args = self._ask_fix(skill, args, err)
                for attempt in range(MAX_RETRY):
                    if fixed_args is None:
                        break
                    self._emit(f"  [retry {attempt+1}/{MAX_RETRY}] 修复后参数: {fixed_args}")
                    try:
                        result = self._invoke(skill, fixed_args)
                        self.session.add_log(StepLog(
                            skill, summary(fixed_args), code + f"\n# retry with {fixed_args}",
                            summary(result), ok=True))
                        return True, summary(result)
                    except Exception as err2:  # noqa: BLE001
                        kind = self.classify(err2)
                        detail = f"{type(err2).__name__}: {err2}"
                        self._emit(f"  [retry {attempt+1} 失败:{kind}] {detail}")
                        fixed_args = None
            # 结构性失败，或机械修复未成功：诚实记录并报告
            self.session.add_log(StepLog(skill, summary(args), code,
                                         None, ok=False,
                                         error=f"[{kind}] {detail}"))
            if kind == "structural":
                return False, (f"结构性错误，自动修复不可行，已停止本步骤。"
                               f"建议检查输入数据或环境。详情: {detail}")
            return False, f"可机械修复的错误重试后仍失败，请检查参数或数据。详情: {detail}"
