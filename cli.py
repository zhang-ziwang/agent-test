"""
命令行外壳 cli.py —— 把 loop.py 的能力包装成终端对话
======================================================
这一层不含任何 Agent 逻辑，只做三件事：
    ① 读用户输入；② 调用 SimpleAgent.run()；③ 把结构化结果打印出来。
因此网页端（server.py）与本端调用的是同一个 Loop，行为完全一致。

特意把「思考 / 工具 / 结果 / 记忆条数」都打印出来，是为了让练习一、二、
三的效果在终端里肉眼可见；不需要时可只打印 reply 一行。
"""
import sys
from pathlib import Path

# 兼容两种启动方式：
#   python -m agent_project.cli   （包方式，推荐，父目录本来就在 sys.path）
#   python agent_project/cli.py   （脚本方式，需手动把父目录加进 sys.path）
_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_project.config import CHAT_MODEL, OLLAMA_BASE
from agent_project.loop import SimpleAgent


def main():
    """命令行主循环：一直读到用户输入退出命令或 Ctrl+C 为止。"""
    agent = SimpleAgent()   # 不传参 -> 自动创建私有 AgentTools + Brain
    print(f"手写 Agent 已启动（模型 {CHAT_MODEL} @ {OLLAMA_BASE}）")
    print("支持天气、计算、联网搜索、待办事项；输入 exit / 退出 结束\n")

    while True:
        # EOFError：管道 / 重定向输入结束；KeyboardInterrupt：Ctrl+C
        try:
            message = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break
        if not message:
            continue
        if message.lower() in ("exit", "quit", "q", "退出"):
            print("已退出。")
            break

        try:
            result = agent.run(message)
        except Exception as e:
            print(f"Agent 执行失败：{e}\n")
            continue

        print(f"  [思考] {result['thought']}")
        print(f"  [工具] {result['tool']}({result['instruction']})")
        print(f"  [结果] {result['observation']}")
        print(f"Agent: {result['reply']}")
        print(f"  [记忆] 已保存 {result['history_length']} 条消息\n")


if __name__ == "__main__":
    main()
