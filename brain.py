"""
大脑模块 brain.py —— 只负责「想」，不负责「做」
=================================================
职责边界：
    输入：用户这一轮说的话 + 历史对话（list[dict]）
    输出：标准 JSON 决策 {"thought": "...", "tool": "...", "instruction": "..."}
    它不调用任何工具，也不直接生成给用户的最终回复；执行交给 loop.py。

为什么强制 JSON（练习二）：
    早期写法用正则从模型的自然语言里抠工具名，模型一改口吻就失效。
    这里让 Ollama 以 format="json" 约束输出，再用 json.loads() 解析；
    解析失败还有两级兜底：提取首个 JSON 对象 -> 降级为 chat 直接回答。

工具清单不写死在提示词里，而是由 tools.describe() 动态生成：
    在 tools.py 里新增工具后，大脑会自动知道它存在，无需改本文件。
"""
import json
import re

import requests

from .config import (
    OLLAMA_BASE,
    CHAT_MODEL,
    TEMPERATURE,
    NUM_CTX,
    REQUEST_TIMEOUT,
    RESPONSE_FORMAT,
    ENABLE_THINKING,
    CONTEXT_TURNS,
)


class Brain:
    """决策大脑：把自然语言意图翻译成结构化的工具调用决策。"""

    def __init__(self, tools):
        """
        :param tools: 任何实现了 describe() 的对象（通常是 AgentTools 实例）。
                      这里用鸭子类型而不是直接 import AgentTools，是为了让
                      大脑不依赖具体工具实现：换一套工具集时本文件不用改。
        """
        self.tools = tools
        self.system_prompt = self._build_prompt()

    def _build_prompt(self):
        """系统提示词 = 输出格式约定 + 动态工具清单 + 行为规则。"""
        return (
            "你是一个 Agent 的决策大脑。只输出一个 JSON 对象，不要解释、不要代码块。\n"
            '格式严格为：{"thought":"简短思考","tool":"工具名","instruction":"传给工具的参数"}\n'
            "可用工具：\n" + self.tools.describe() + "\n"
            "规则：\n"
            "1. 查天气、计算、联网搜索、增删查待办时选对应工具，instruction 只放参数本身。\n"
            "2. 普通闲聊、解释、总结选 chat，instruction 填你要对用户说的话。\n"
            "3. tool 必须是清单里的名字；instruction 必须是字符串，无参数时填空字符串。\n"
            "4. 涉及「刚才」「之前」「第几个」等指代时，只能依据对话历史里的 observation "
            "字段回答，严禁编造工具未返回的数值或结果；历史里记录的是错误就如实说明。\n"
        )

    def think(self, prompt, history=None):
        """
        练习一的关键：发送当前提示的同时，把历史对话一起作为上下文发送。

        :param prompt:  用户这一轮的输入（str）
        :param history: [{"role": "user"|"assistant", "content": "..."}, ...]
        :return: {"thought": str, "tool": str, "instruction": str}
        """
        # Ollama 的消息格式要求 system 在最前、user 在最后，中间按时间顺序放历史
        messages = [{"role": "system", "content": self.system_prompt}]
        for turn in (history or [])[-CONTEXT_TURNS:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": prompt})

        # 直接走 Ollama 原生 HTTP 接口，不引入 LangChain：
        # 这样「决策 = 一次 HTTP 请求 + 一次 json.loads」的链路对学习者是透明的。
        resp = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": CHAT_MODEL,
                "stream": False,
                "format": RESPONSE_FORMAT,   # 强制合法 JSON
                "think": ENABLE_THINKING,    # 关闭思考链，避免污染 JSON
                "messages": messages,
                "options": {"temperature": TEMPERATURE, "num_ctx": NUM_CTX},
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]

        # ---- 第一级：直接解析（format=json 时基本必然成功）----
        try:
            decision = json.loads(raw)
        except json.JSONDecodeError:
            # ---- 第二级：模型偶尔会包一层说明文字，抠出第一个 JSON 对象 ----
            match = re.search(r"\{.*\}", raw, re.S)
            try:
                decision = json.loads(match.group(0)) if match else {}
            except json.JSONDecodeError:
                decision = {}
        if not isinstance(decision, dict):
            decision = {}

        # ---- 字段归一化：无论模型怎么输出，下游拿到的都是这三个确定字段 ----
        thought = str(decision.get("thought", "")).strip()
        tool = str(decision.get("tool", "")).strip() or "chat"
        instruction = decision.get("instruction", "")
        if not isinstance(instruction, str):
            # 模型有时把无参数写成 {}，统一转成字符串，避免工具侧类型报错
            instruction = json.dumps(instruction, ensure_ascii=False)

        return {"thought": thought, "tool": tool, "instruction": instruction}
