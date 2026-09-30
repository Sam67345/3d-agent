"""Session：agent 中间状态的单一持久化对象。

所有技能执行结果都挂在这里；支持 pickle 落盘恢复，并记录 notebook 导出所需的
步骤日志（代码 + 结果摘要）。
"""

import pickle
import time
from pathlib import Path

import numpy as np


def _plain(obj, depth=0):
    """把结果对象转成可 JSON 化的摘要（截断大对象）。"""
    if depth > 4:
        return "..."
    try:
        import anndata as ad
        if isinstance(obj, ad.AnnData):
            return f"AnnData({obj.n_obs} obs x {obj.n_vars} vars)"
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _plain(v, depth + 1) for k, v in obj.items()
                if k not in ("adata1", "adata2", "virtual_slice")}
    if isinstance(obj, (list, tuple)):
        return [_plain(v, depth + 1) for v in obj[:20]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, np.generic):
        return obj.item()
    return str(type(obj).__name__)


class StepLog:
    """一步执行的记录（导出 notebook 的原料）。"""

    def __init__(self, skill, args_summary, code, result_summary, ok, error=None):
        self.skill = skill
        self.args_summary = args_summary
        self.code = code
        self.result_summary = result_summary
        self.ok = ok
        self.error = error
        self.time = time.strftime("%Y-%m-%d %H:%M:%S")


class Session:
    """持有 agent 的全部中间状态。"""

    def __init__(self, workdir=None):
        self.workdir = Path(workdir) if workdir else Path(__file__).resolve().parent.parent
        self.loaded = {}          # sample_id -> AnnData（预处理前）
        self.pair = None          # (adata1, adata2) 预处理切片对（前两张，向后兼容）
        self.pair_meta = None
        self.slices = []          # M6.3：全部已加载切片（按用户给定顺序），len >= 2
        self.virtual_slices = []  # list[AnnData]
        self.dense = None         # 合并后的多层 AnnData
        self.outputs = {}         # 名字 -> 路径
        self.logs = []            # list[StepLog]
        self.run_dir = None       # M6.4：本会话输出目录（output/run_时间戳）

    # ---- 状态查询（Verifier 用） ----
    @property
    def has_pair(self):
        return self.pair is not None

    @property
    def has_slices(self):
        """M6.3：>= 2 张已加载切片（老会话只有 pair 时也视为满足）。"""
        return len(getattr(self, "slices", []) or []) >= 2 or self.has_pair

    @property
    def all_slices(self):
        """按顺序返回全部已加载切片（slices 优先，回退 pair）。"""
        s = getattr(self, "slices", None) or []
        if s:
            return list(s)
        return list(self.pair) if self.pair is not None else []

    @property
    def has_virtual(self):
        return len(self.virtual_slices) > 0

    @property
    def has_dense(self):
        return self.dense is not None

    def add_log(self, step):
        self.logs.append(step)

    # ---- 持久化 ----
    def save(self, path=None):
        path = Path(path) if path else self.workdir / "session" / "session.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        return str(path)

    @staticmethod
    def load(path):
        with open(path, "rb") as f:
            return pickle.load(f)


def summary(obj):
    return _plain(obj)
