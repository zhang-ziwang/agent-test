"""
循环模块 loop.py —— Agent 的主循环，负责把「想」和「做」串起来
================================================================
这是整个项目的中枢，三个练习的落点都在这里：

    练习一  记忆：self.conversation_history = []
              调用 Brain 时把历史一起发过去，执行后把本回合写回历史。
    练习二  自动工具选择：拿到 Brain 返回的 JSON 决策，按 tool 字段查分派表。
    练习三  新工具：在 _use_tool() 的分派表里注册 todo_add / todo_show。

一次 run() 的完整数据流：

    用户输入
      -> Brain.think(输入, 历史)         得到 {thought, tool, instruction}
      -> _use_tool(tool, instruction)    得到 observation（工具结果）
      -> 把 user / thought / observation / answer 写入 conversation_history
      -> 返回结构化 dict（命令行与网页共用同一份结果）

为什么把「循环」单独成文件：
    命令行、网页、甚至以后的微信机器人，都只是不同的「外壳」；
    只要共用这一个 SimpleAgent，各端的行为就完全一致，不用重复实现记忆和分派。
"""
from .brain import Brain
from .config import MAX_HISTORY
from .tools import AgentTools


class SimpleAgent:
    """有记忆、能自动选工具的手写 Agent。"""

    def __init__(self, tools=None, brain=None):
        """
        :param tools: AgentTools 实例。Web 端会传入一个共享实例，让所有设备
                      看到同一份待办；命令行不传则自动创建私有实例。
        :param brain: Brain 实例。不传则按 tools 自动创建。
        """
        self.tools = tools or AgentTools()
        self.brain = brain or Brain(self.tools)

        # 练习一：对话记忆。每条是 {"role": "...", "content": "..."}，
        # 与 Ollama 的 messages 格式一致，因此可以直接作为上下文发送。
        self.conversation_history = []

    def _use_tool(self, name, instruction):
        """
        工具分派表（练习三的登记处）。

        新增工具的步骤：
            1. 在 tools.py 里实现方法；
            2. 在 tools.describe() 里加一行说明，让大脑知道它；
            3. 在下面这张表里加一项。
        """
        table = {
            "weather": self.tools.weather,
            "calculator": self.tools.calculator,
            "search": self.tools.search,
            "todo_add": self.tools.add_todo,
            # todo_show 不需要参数，用 lambda 吞掉大脑传来的空字符串 / "{}"
            "todo_show": lambda _="": self.tools.show_todos(),
            # chat 是「伪工具」：不产生副作用，直接把大脑写好的话当作结果返回
            "chat": lambda text: text,
        }
        func = table.get(name)
        if func is None:
            # 小模型偶尔会写错工具名，这里兜底而不是让整轮对话崩掉
            return f"未知工具 '{name}'；可用工具：{', '.join(table)}"
        try:
            return str(func(instruction)) or "（工具未返回内容）"
        except Exception as e:
            return f"工具 '{name}' 执行失败：{e}"

    def run(self, user_input):
        """
        执行一个对话回合。

        :return: dict，包含 reply / thought / tool / instruction /
                 observation / history_length。
                 返回结构化 dict 而不是只返回字符串，是为了让 CLI 能打印
                 思考过程、网页能展示 JSON 决策链路，而不用各自解析文本。
        """
        # ① 决策：当前输入 + 历史一起交给大脑
        decision = self.brain.think(user_input, self.conversation_history)

        # ② 执行：按 JSON 里的 tool 字段自动选择工具
        observation = self._use_tool(decision["tool"], decision["instruction"])

        # ③ 回复：本实现保持「工具结果即回复」，链路最透明；
        #    若想让模型把工具结果润色成自然语言，可在这里再调用一次 Brain。
        answer = observation

        # ④ 练习一：把新回合写入历史。
        #    一条 user + 一条 assistant；assistant 里带 thought / observation，
        #    下一轮模型才能回答「我刚才算了什么」这类指代问题。
        self.conversation_history.append({"role": "user", "content": user_input})
        self.conversation_history.append({
            "role": "assistant",
            "content": (
                f"thought={decision['thought']} | tool={decision['tool']} | "
                f"instruction={decision['instruction']} | "
                f"observation={observation} | answer={answer}"
            ),
        })
        # 只保留最近 MAX_HISTORY 条，避免长对话把上下文窗口撑爆
        self.conversation_history = self.conversation_history[-MAX_HISTORY:]

        return {
            "reply": answer,
            "thought": decision["thought"],
            "tool": decision["tool"],
            "instruction": decision["instruction"],
            "observation": observation,
            "history_length": len(self.conversation_history),
        }

    def clear(self):
        """清空对话记忆（不动共享的待办数据），供网页端「清空记忆」按钮调用。"""
        self.conversation_history = []
