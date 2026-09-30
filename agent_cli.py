"""Agent 主循环：Planner → Verifier → Executor(+Error Reflector)。

CLI 入口（注意：不能叫 agent.py，会与 agent/ 包冲突）：
  python agent_cli.py                 # 交互 REPL
  python agent_cli.py "指令"          # 单条指令
  python agent_cli.py --mock "指令"   # 无密钥测试（确定性 MockLLM）
"""

import sys
from pathlib import Path

# Windows GBK 控制台 / pythonw 无控制台环境下，emoji 打印会抛 UnicodeEncodeError。
# 统一把 stdout/stderr 设为 errors='replace'；pythonw 下 stdout 为 None 则换空设备。
import io, os

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.executor import Executor
from agent.llm import LLMClient, LLMConfigError, MockLLM, check_config
from agent.planner import Planner
from agent.registry import load_registry
from agent.session import Session
from agent.verifier import verify


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

    def handle(self, user_text):
        """处理一条自然语言指令。返回 (ok, message)。"""
        print(f"\n🧠  Planner 解析: {user_text}")
        try:
            steps, reply, dropped = self.planner.plan(user_text, self.session)
        except Exception as e:  # noqa: BLE001
            return False, f"Planner 失败（可能是 LLM 输出非 JSON）: {e}"
        if dropped:
            print(f"  ⚠️  丢弃无效步骤: {dropped}")
        if reply:
            print(f"  💬 {reply}")
        if not steps:
            return False, "Planner 未产出可执行步骤（可能需要用户澄清）"

        # 依赖规范化（可靠性来自架构而非 prompt）：需要 dense 数据的步骤前
        # 若已有虚拟切片但未合并，自动补 make_dense
        expanded = []
        for s in steps:
            if s["skill"] in ("validate", "visualize_3d") \
                    and not self.session.has_dense and self.session.has_virtual \
                    and all(x["skill"] != "make_dense" for x in expanded + steps):
                expanded.append({"skill": "make_dense", "args": {},
                                 "reason": "自动补全依赖：合并多层数据"})
            expanded.append(s)
        steps = expanded

        # 参数白名单过滤（可靠性来自架构而非 prompt）：LLM 编造的技能参数
        # 先尝试映射到最接近的已声明参数（M7.1），映射不了才丢弃并告知，
        # 而不是留给运行期报 TypeError
        from agent.executor import normalize_args
        for s in steps:
            s["args"], _notes = normalize_args(s["skill"], s.get("args", {}),
                                               self.registry, emit=print)

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
                return False, msg

            print(f"  ⚙️  Executor 执行: {skill}({args})")
            ok, message = self.executor.run_step(skill, args)
            if not ok:
                return False, f"步骤 {skill} 失败: {message}"
            print(f"  ✅ 完成: {message}")
        return True, f"完成 {len(steps)} 个步骤"


BANNER = """\
=== SpatialZ 3D 插值 Agent（M4 MVP）===
自然语言指令驱动：加载 / 概况 / 对齐 / 插值 / 可视化 / 验证 / 导出notebook
输入 exit 退出
"""


def main():
    import time
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mock = "--mock" in sys.argv
    resume = "--resume" in sys.argv

    # M6.4：每次启动创建本会话专属输出目录（显式传路径仍优先于该默认值）
    run_dir = Path("output") / time.strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        agent = Agent(mock=mock, run_dir=run_dir)
    except LLMConfigError as e:
        print(f"配置错误: {e}")
        sys.exit(2)

    if resume:
        from agent.session import Session
        pkl = Path("session/session.pkl")
        if pkl.exists():
            try:
                agent.session = Session.load(pkl)
                agent.executor.session = agent.session
                if not getattr(agent.session, "run_dir", None):
                    agent.session.run_dir = run_dir
                print(f"会话已从 {pkl} 恢复（虚拟切片 "
                      f"{len(agent.session.virtual_slices)} 层）")
            except Exception as e:  # noqa: BLE001
                print(f"会话恢复失败（开始新会话）: {e}")

    print(BANNER)
    print(f"本会话输出目录: {agent.session.run_dir}")
    if args:
        ok, msg = agent.handle(" ".join(args))
        print(msg)
        sys.exit(0 if ok else 1)

    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "退出"):
            path = agent.session.save()
            print(f"会话已保存: {path}")
            break
        ok, msg = agent.handle(text)
        print(msg)


if __name__ == "__main__":
    main()
