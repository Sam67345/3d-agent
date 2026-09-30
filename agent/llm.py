"""LLM 客户端：OpenAI 兼容接口（chat/completions）。

密钥只读环境变量，绝不写死：
  MY_LLM_API_KEY   API key
  MY_LLM_BASE_URL  网关地址，如 https://api.deepseek.com/v1
  MY_LLM_MODEL     模型名，如 deepseek-chat

也支持项目根目录 .env 文件（python-dotenv，优先级低于真实环境变量）。
"""

import json
import os
import re
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass

import requests

_ENV_VARS = ("MY_LLM_API_KEY", "MY_LLM_BASE_URL", "MY_LLM_MODEL")


class LLMConfigError(RuntimeError):
    """环境变量缺失或配置非法。"""


def check_config():
    """返回缺失的环境变量列表（空列表 = 配置就绪）。"""
    return [v for v in _ENV_VARS if not os.environ.get(v)]


class LLMClient:
    """OpenAI 兼容 chat/completions 客户端（带边界重试）。"""

    def __init__(self, timeout=120, max_retries=2):
        missing = check_config()
        if missing:
            raise LLMConfigError(
                f"缺少环境变量: {', '.join(missing)}。"
                f"请设置 {_ENV_VARS} 或在项目根目录放置 .env 文件。")
        self.api_key = os.environ["MY_LLM_API_KEY"]
        self.base_url = os.environ["MY_LLM_BASE_URL"].rstrip("/")
        self.model = os.environ["MY_LLM_MODEL"]
        self.timeout = timeout
        self.max_retries = max_retries

    def chat(self, messages, temperature=0.2):
        """调用 chat/completions，返回 assistant 文本。失败重试 max_retries 次。"""
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature}

        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, data=json.dumps(payload),
                                     timeout=self.timeout)
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (requests.RequestException, RuntimeError, KeyError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败（重试 {self.max_retries} 次后放弃）: {last_err}")


def extract_json(text):
    """从 LLM 回复中提取第一个 JSON 对象/数组（容忍 ```json 围栏）。"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = m.group(1) if m else text
    # 找第一个 { 或 [ 到最后一个 } 或 ]
    start = min([i for i in (candidate.find("{"), candidate.find("["))
                 if i != -1], default=-1)
    if start == -1:
        raise ValueError(f"LLM 回复中未找到 JSON: {text[:200]}")
    opener = candidate[start]
    closer = "}" if opener == "{" else "]"
    end = candidate.rfind(closer)
    if end <= start:
        raise ValueError(f"LLM 回复中 JSON 不完整: {text[:200]}")
    return json.loads(candidate[start:end + 1])


class MockLLM:
    """测试用确定性 LLM：按关键词路由到内置计划，不发起网络请求。

    用于无密钥环境下验证 agent 闭环的机械链路（Planner→Verifier→Executor）。
    """

    # 顺序即优先级：长词/特定意图在前，避免"验证插值质量"被"插值"抢先命中
    PLANS = {
        "导出notebook": [{"skill": "export_notebook", "args": {}}],
        "可视化": [{"skill": "visualize_3d", "args": {}}],
        "验证": [{"skill": "validate", "args": {}}],
        "概况": [{"skill": "overview", "args": {}}],
        "加载": [{"skill": "load_slices", "args": {"n_hvg": 2000}}],
        "对齐": [{"skill": "interpolate", "args": {"alpha": 0.5, "nb_iter": 50}}],
        "插值": [
            {"skill": "interpolate", "args": {"alpha": 0.333, "nb_iter": 50}},
            {"skill": "interpolate", "args": {"alpha": 0.667, "nb_iter": 50}},
        ],
    }

    def chat(self, messages, temperature=0.2):
        text = messages[-1]["content"]
        # 只匹配用户指令本身（系统拼接的状态文本可能含关键词，如"已加载"）
        if "用户指令：" in text:
            text = text.split("用户指令：", 1)[1].split("\n", 1)[0]
        for kw, plan in self.PLANS.items():
            if kw in text:
                return json.dumps({"steps": plan}, ensure_ascii=False)
        return json.dumps({"steps": [], "reply": "我不理解这个指令。"},
                          ensure_ascii=False)
