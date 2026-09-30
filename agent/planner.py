"""Planner：把用户自然语言指令拆成有序技能步骤（JSON 计划）。

可靠性原则：Planner 只负责"选技能+填参数"，参数合法性由 Verifier 与技能自身兜底。
"""

import json

from .llm import extract_json
from .registry import routing_table

SYSTEM_PROMPT = """你是空间转录组 3D 插值系统的 Planner。把用户指令拆成有序的技能调用计划。
可用技能：
{table}

严格只输出 JSON（不要输出其他文字）：
{{"steps": [{{"skill": "技能名", "args": {{...}}, "reason": "一句话理由"}}],
 "reply": "给用户的简短说明（可选）"}}
规则：
- 步骤顺序必须符合依赖：load_slices 先于 interpolate；interpolate 先于 make_dense/validate/visualize_3d
- 参数只填技能 inputs 里声明的，不要编造参数
- 数据对象参数（interpolate 的 adata1/adata2、validate 的 order/names、visualize_3d 的 adata）
  由系统自动从会话状态注入，绝对不要写进 args
- 一次指令只做必要的事；不确定时输出空 steps 并在 reply 里向用户澄清
"""


class Planner:
    def __init__(self, llm, registry):
        self.llm = llm
        self.registry = registry

    def plan(self, user_text, session):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(table=routing_table(self.registry))},
            {"role": "user", "content": f"用户指令：{user_text}\n"
                                        f"当前状态：已加载切片对={session.has_pair}, "
                                        f"已有虚拟切片={len(session.virtual_slices)}层, "
                                        f"已合并dense={session.has_dense}"},
        ]
        raw = self.llm.chat(messages, temperature=0.2)
        data = extract_json(raw)
        steps = data.get("steps", [])
        # 机械过滤：未知技能 / 非法结构直接丢弃并说明
        valid, dropped = [], []
        for s in steps:
            name = s.get("skill")
            if name in self.registry and isinstance(s.get("args", {}), dict):
                valid.append(s)
            else:
                dropped.append(s)
        return valid, (data.get("reply") or ""), dropped
