"""
SimpleAgent 项目包 —— 一个按职责拆分的手写 Agent 工程
========================================================
目录结构（全部文件都在 agent_project/ 这一个文件夹里）：

    agent_project/
    ├── __init__.py     本文件：包说明与统一导出
    ├── config.py       配置层：所有会变的值（模型、端口、UA、路径、鉴权开关）
    ├── brain.py        大脑层：只「想」，输出 JSON 决策
    ├── tools.py        工具层：只「做」，返回结果字符串
    ├── loop.py         循环层：记忆 + 决策 + 分派（项目中枢）
    ├── auth.py         鉴权层：API Key 的签发、哈希存储与校验
    ├── cli.py          交互外壳一：命令行对话
    ├── server.py       交互外壳二：局域网 Web 服务（含鉴权与签发接口）
    ├── web/
    │   └── index.html  前端页面（申请/验证 Key + 聊天 + 待办 + 决策链路）
    └── data/
        └── api_keys.json  运行时生成的 key 哈希表（只存哈希，应加进 .gitignore）

依赖关系（自上而下，无反向依赖）：

    cli.py / server.py        两种外壳，共用同一个 Loop
            │
            v
         loop.py  ──────>  brain.py  ──────>  config.py
            │                                    ^
            └──────────>  tools.py  ────────────┘

设计原则：
  1. Brain 不碰工具，Tools 不知道模型存在，两者可独立测试；
  2. Loop 是唯一中枢，换前端不用改记忆与工具分派逻辑；
  3. config.py 是依赖树最底层，不 import 项目内任何模块。

运行方式（在 agent-test 目录下）：
    python simple_agent.py          旧入口（转发到 agent_project.cli）
    python web_server.py            旧入口（转发到 agent_project.server）
    python -m agent_project.cli     包方式启动命令行
    python -m agent_project.server  包方式启动网页服务

三个练习的落点：
    练习一 记忆      -> loop.py 的 conversation_history 与 brain.think(history)
    练习二 JSON 决策 -> brain.py 的 format="json" + json.loads()
    练习三 待办工具  -> tools.py 的 todo_list/add_todo/show_todos + loop.py 分派表
"""
__version__ = "1.0.0"

from .brain import Brain
from .loop import SimpleAgent
from .tools import AgentTools

__all__ = ["SimpleAgent", "Brain", "AgentTools", "__version__"]
