"""Agent 主循环：Planner -> Verifier -> Executor(+Error Reflector)。

M16：从 agent_cli.py 搬入 agent 包。原先 Agent 类定义在 CLI 入口文件里，
web_app / desktop_app 都从 agent_cli import Agent，导致 CLI 版本一旦归档
整个桌面端就断链。搬进包内后，入口文件（已归档）与内核彻底解耦。
"""

from pathlib import Path

from .executor import Executor
from .llm import LLMClient, LLMConfigError, MockLLM, check_config
from .planner import Planner
from .registry import load_registry
from .session import Session
from .verifier import verify


class Agent:
    def __init__(self, mock=False, verbose=True, run_dir=None):
        self.registry = load_registry()
        self.session = Session()
        self.session.run_dir = Path(run_dir) if run_dir else None
        if mock:
            self.llm = MockLLM()
        else:
            missing = check_config()
            if missing:
                raise LLMConfigError(
                    f"缺少环境变量: {', '.join(missing)}。请先设置后再启动，"
                    f"或用 --mock 体验无 LLM 的闭环演示。")
            self.llm = LLMClient()
        self.planner = Planner(self.llm, self.registry)
        self.executor = Executor(self.session, llm=self.llm, verbose=verbose)

    def _emit(self, event):
        """M8：步骤级事件回调（UI 用它做流式展示）；未设置时为空操作。"""
        cb = getattr(self, "on_event", None)
        if cb:
            try:
                cb(event)
            except Exception:  # noqa: BLE001 - UI 回调异常不得影响执行
                pass

    def prepare_steps(self, user_text):
        """只规划不执行：返回 (steps, reply, dropped)，已做依赖补全与参数规整。

        M8 新增：UI 的「计划确认卡片」用它拿到步骤表，用户确认后再交给 run_steps。
        """
        print(f"\n🧠  Planner 解析: {user_text}")
        try:
            steps, reply, dropped = self.planner.plan(user_text, self.session)
        except Exception as e:  # noqa: BLE001
            return [], f"Planner 失败（可能是 LLM 输出非 JSON）: {e}", []
        if dropped:
            print(f"  ⚠️  丢弃无效步骤: {dropped}")
        if reply:
            print(f"  💬 {reply}")
        if not steps:
            return [], reply or "", dropped

        # 依赖规范化（可靠性来自架构而非 prompt）：需要 dense 数据的步骤前
        # 若已有虚拟切片但未合并，自动补 make_dense
        expanded = []
        for s in steps:
            if s["skill"] in ("validate", "visualize_3d") \
                    and not self.session.has_dense and self.session.has_virtual \
                    and all(x["skill"] != "make_dense" for x in expanded + steps):
                expanded.append({"skill": "make_dense", "args": {},
                                 "reason": "自动补全依赖：合并多层数据"})
                self._emit({"type": "auto_step", "skill": "make_dense"})
            expanded.append(s)
        steps = expanded

        # 参数白名单过滤（可靠性来自架构而非 prompt）：LLM 编造的技能参数
        # 先尝试映射到最接近的已声明参数（M7.1），映射不了才丢弃并告知，
        # 而不是留给运行期报 TypeError
        from agent.executor import normalize_args
        for s in steps:
            s["args"], _notes = normalize_args(s["skill"], s.get("args", {}),
                                               self.registry, emit=print)
        return steps, reply or "", dropped

    def run_steps(self, steps, on_event=None):
        """按序执行已规划好的步骤。返回 (ok, message)。

        M8 新增 on_event：每步开始/完成/被拦截都会回调，供 UI 流式展示。
        """
        self.on_event = on_event
        try:
            return self._run_steps(steps)
        finally:
            self.on_event = None

    def _run_steps(self, steps):
        import time

        for s in steps:
            skill, args = s["skill"], s.get("args", {})
            meta = self.registry[skill]

            # Verifier：逐项检查前置条件，缺失则提问澄清，禁止编造
            ok, problems = verify(skill, meta, self.session, args)
            problems = list(dict.fromkeys(problems))  # 去重
            if not ok:
                msg = (f"前置条件不满足，无法执行 {skill}：\n  - "
                       + "\n  - ".join(problems)
                       + "\n请补充信息或先执行依赖步骤。")
                print(f"  🚫 Verifier 拦截: {problems}")
                self._emit({"type": "verifier_block", "skill": skill,
                            "problems": problems})
                return False, msg

            self._emit({"type": "step_start", "skill": skill, "args": dict(args)})
            print(f"  ⚙️  Executor 执行: {skill}({args})")
            t0 = time.time()
            ok, message = self.executor.run_step(skill, args)
            elapsed = round(time.time() - t0, 1)
            if not ok:
                self._emit({"type": "step_fail", "skill": skill,
                            "detail": str(message), "elapsed_sec": elapsed})
                return False, f"步骤 {skill} 失败: {message}"
            print(f"  ✅ 完成: {message}")
            self._emit({"type": "step_done", "skill": skill,
                        "result": message if isinstance(message, dict) else {},
                        "elapsed_sec": elapsed})
        return True, f"完成 {len(steps)} 个步骤"

    def handle(self, user_text, on_event=None):
        """处理一条自然语言指令（规划 + 执行）。返回 (ok, message)。

        M8：on_event 可选，透传给 run_steps 做步骤级流式展示。
        """
        steps, reply, _dropped = self.prepare_steps(user_text)
        if not steps:
            return False, reply or "Planner 未产出可执行步骤（可能需要用户澄清）"
        return self.run_steps(steps, on_event=on_event)
