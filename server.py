"""
Web 外壳 server.py —— 把同一个 Loop 包装成局域网可访问的网页服务
================================================================
只用 Python 标准库 http.server，不依赖 Flask / FastAPI（环境里没装）。
监听 0.0.0.0:8000，同一 Wi-Fi 下的手机、平板可以直接访问。

会话设计（重要）：
    SHARED_TOOLS  全局唯一一份 AgentTools  -> 待办事项在所有设备上共享
    SESSIONS      每个 session_id 一个 SimpleAgent -> 每台设备对话记忆独立
    这样 A 手机加的待办 B 手机能看到，但 A 的聊天上下文不会串到 B。

HTTP 接口：
    GET  /                 返回前端页面（开放）
    GET  /api/info         局域网 IP、端口、模型名、会话数、key 数量（开放）
    GET  /api/todos        共享待办列表（需 API Key）
    POST /api/chat         {"message": "...", "session_id": "..."} -> 一轮对话（需 API Key）
    POST /api/clear        清空指定会话的记忆（需 API Key）
    POST /api/key/issue    自助签发 API Key，返回明文一次（开放）
    POST /api/key/verify   校验 API Key 是否有效，返回 {"valid": bool}（开放）

鉴权约定：
    * 业务接口统一从 Authorization: Bearer <key> 取 key，交给 auth.py 查哈希表；
    * 签发与校验接口必须开放，否则新设备拿不到 key、也无法完成首次验证；
    * 页面上所有人都能自助申请 key，因此这一步是「登记」不是「授权」，
      需要真正授权时应给 /api/key/issue 再加一道管理员口令（见下方注释）。
"""
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# 与 cli.py 同理：兼容 python -m agent_project.server 与直接运行脚本两种方式
_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_project.auth import APIKeyStore
from agent_project.brain import Brain
from agent_project.config import (
    API_KEY_STORE, AUTH_ENABLED, BIND_HOST, CHAT_MODEL, INDEX_HTML, PORT,
)
from agent_project.loop import SimpleAgent
from agent_project.tools import AgentTools

# API Key 哈希表：磁盘上只存哈希，明文只出现在签发响应里
KEY_STORE = APIKeyStore(API_KEY_STORE)

# 所有设备共享一份工具（待办全局可见）
SHARED_TOOLS = AgentTools()
# session_id -> SimpleAgent。字典本身会被多线程访问，用锁保护创建过程。
SESSIONS = {}
LOCK = threading.Lock()


def get_agent(session_id):
    """按设备返回对应的 Agent；首次访问时懒创建。"""
    with LOCK:
        if session_id not in SESSIONS:
            SESSIONS[session_id] = SimpleAgent(
                tools=SHARED_TOOLS, brain=Brain(SHARED_TOOLS)
            )
        return SESSIONS[session_id]


def lan_ip():
    """取本机对外的局域网 IP：连一个 UDP socket 但不实际发包。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    """一个极简的静态文件 + JSON API 处理器。"""
    protocol_version = "HTTP/1.1"   # 手机浏览器对 keep-alive 更友好

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))  # HTTP/1.1 必须给
        # 允许局域网内其他来源调试；Authorization 必须列入，否则预检会拦掉带 key 的请求
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def log_message(self, fmt, *args):
        return  # 静默；想看请求日志可注释掉这一行

    def _require_auth(self):
        """统一鉴权：取 Bearer key -> 算哈希 -> 查表。失败时已写好 401 响应。"""
        if not AUTH_ENABLED:
            return True
        token = self.headers.get("Authorization", "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        else:
            token = token.strip()
        if token and KEY_STORE.verify(token):
            return True
        self._json({"error": "缺少或无效的 API Key，请先在页面上方申请并填入"}, 401)
        return False

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "缺少 agent_project/web/index.html", "text/plain; charset=utf-8")
        elif path == "/api/todos":
            if not self._require_auth():
                return
            self._json({"todos": SHARED_TOOLS.get_todos()})
        elif path == "/api/info":
            self._json({
                "lan_ip": lan_ip(), "port": PORT,
                "model": CHAT_MODEL, "sessions": len(SESSIONS),
                "keys": KEY_STORE.count(), "auth": AUTH_ENABLED,
            })
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "请求体不是合法 JSON"}, 400)
            return

        # ---- ① 自助签发：服务端生成随机串，只把哈希写进表 ----
        if path == "/api/key/issue":
            # 按需求开放。若要变成真正的「授权」，在这里加一道管理员口令，例如：
            # if payload.get("admin_secret") != ADMIN_SECRET:
            #     self._json({"error": "无权签发"}, 403); return
            key = KEY_STORE.issue(label=str(payload.get("label", "")))
            self._json({
                "api_key": key,
                "hint": "明文只显示这一次，请复制保存；服务端只保存哈希",
            })
            return

        # ---- ② 校验：前端把输入的 key 送回来，服务端查表 ----
        if path == "/api/key/verify":
            token = str(payload.get("api_key", "")).strip()
            self._json({"valid": KEY_STORE.verify(token)} if token else {"valid": False})
            return

        # ---- 以下业务接口都需要有效 key ----
        if path == "/api/chat":
            if not self._require_auth():
                return
            message = str(payload.get("message", "")).strip()
            if not message:
                self._json({"error": "message 不能为空"}, 400)
                return
            sid = str(payload.get("session_id", "default"))
            try:
                self._json(get_agent(sid).run(message))
            except Exception as e:
                self._json({"error": f"Agent 执行失败：{e}"}, 500)
        elif path == "/api/clear":
            if not self._require_auth():
                return
            get_agent(str(payload.get("session_id", "default"))).clear()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def main():
    """启动服务并打印局域网访问地址。"""
    ip = lan_ip()
    print("=" * 62)
    print("  手写 Agent 网页服务已启动")
    print(f"  本机访问：  http://127.0.0.1:{PORT}")
    print(f"  局域网访问：http://{ip}:{PORT}")
    print(f"  模型：      {CHAT_MODEL}（本地 Ollama）")
    print(f"  API Key：   已启用，现有 {KEY_STORE.count()} 把（页面上方自助申请）")
    print("  其他设备需与本机同一 Wi-Fi；连不上时检查 Windows 防火墙是否放行 Python")
    print("=" * 62)
    server = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
