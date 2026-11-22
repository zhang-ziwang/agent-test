"""
工具模块 tools.py —— 只负责「做」，不负责「想」
=================================================
职责边界：
    输入：一个参数字符串
    输出：一个结果字符串
    工具完全不知道模型存在，也不知道自己是被谁调用的；
    这样每个方法都可以单独用 Python 直接测试，不依赖 Ollama。

四个层次：
    describe()   把能力清单交给 brain.py 注入提示词（新增工具后大脑自动感知）
    weather()    真实天气：wttr.in 的 JSON 接口，免 API Key
    calculator() 安全的 AST 求值，不用 eval()（避免任意代码执行）
    search()     真实联网搜索：360 移动版为主源，Bing RSS 为兜底
    todo_*       练习三新增的待办事项工具

约定：所有工具方法都返回 str，且内部自己吞掉异常返回可读的错误文案，
      这样一次工具失败不会让整个 Agent 循环崩溃。
"""
import ast
import operator
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse, parse_qs

import requests
from lxml import html

from .config import HEADERS, HTTP_TIMEOUT


class AgentTools:
    """工具集。练习三要求在这里放 todo_list / add_todo() / show_todos()。"""

    def __init__(self):
        # 对应练习里的 todo_list = []。
        # 放在实例属性而不是类属性，是为了避免多个 Agent 实例共享同一份列表、
        # 导致 A 设备加的待办出现在 B 设备上（Web 端是靠「共享本实例」来显式共享的）。
        self.todo_list = []

    def describe(self):
        """
        工具清单会原样注入 Brain 的系统提示词。
        新增工具时：① 在下面加一行说明；② 在 loop.py 的分派表里加一项。
        """
        return (
            "- weather     : 查询城市实时天气，instruction 填城市名，如 '北京'\n"
            "- calculator  : 计算数学表达式，instruction 填表达式，如 '15*37+228'\n"
            "- search      : 联网搜索事实性信息，instruction 填 2~5 个核心关键词\n"
            "- todo_add    : 添加待办事项，instruction 填待办内容\n"
            "- todo_show   : 查看全部待办，instruction 填空字符串\n"
            "- chat        : 直接回答或闲聊，instruction 填要说的中文内容\n"
        )

    # ------------------------------------------------------------------
    # 一、天气：wttr.in 的 format=j1 返回结构化 JSON，免 Key
    # ------------------------------------------------------------------
    def weather(self, city):
        city = (city or "北京").strip()
        try:
            data = requests.get(
                f"https://wttr.in/{quote(city)}?format=j1",
                headers={"User-Agent": "curl"},   # wttr.in 对 curl UA 稳定返回 JSON
                timeout=HTTP_TIMEOUT,
            ).json()
            cur = data["current_condition"][0]
            # 注意：weatherDesc 是英文（如 Smoke），模型会在回答时自然译成中文
            return (f"{city} 当前天气：{cur['weatherDesc'][0]['value']}，"
                    f"气温 {cur['temp_C']}℃，体感 {cur['FeelsLikeC']}℃，"
                    f"湿度 {cur['humidity']}%，风速 {cur['windspeedKmph']} km/h")
        except Exception as e:
            return f"天气获取失败：{e}"
        # 能力边界：wttr.in 只给当前实况和未来 2~3 天（data["weather"]），
        # 不提供历史天气；需要指定日期预报时可在这里扩展解析 weather 数组。

    # ------------------------------------------------------------------
    # 二、计算：白名单 AST 求值，绝不使用 eval()
    # ------------------------------------------------------------------
    def calculator(self, expression):
        # 只允许这几个运算符对应的 AST 节点，其余一律拒绝
        ops = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Pow: operator.pow, ast.USub: operator.neg,
        }

        def safe_eval(node):
            if isinstance(node, ast.Expression):
                return safe_eval(node.body)
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.BinOp):
                func = ops.get(type(node.op))
                if func is None:
                    raise ValueError(f"不支持的运算符 {type(node.op).__name__}")
                return func(safe_eval(node.left), safe_eval(node.right))
            if isinstance(node, ast.UnaryOp):
                func = ops.get(type(node.op))
                if func is None:
                    raise ValueError(f"不支持的运算符 {type(node.op).__name__}")
                return func(safe_eval(node.operand))
            raise ValueError(f"不支持的表达式类型：{type(node).__name__}")

        try:
            # 模型常把幂写成 ^，而 Python 里 ^ 是按位异或；
            # 顺手兼容全角乘除号，避免「2 的 10 次方」算成错误结果。
            expr = (expression or "").replace("^", "**").replace("×", "*").replace("÷", "/")
            result = safe_eval(ast.parse(expr, mode="eval"))
            return f"计算结果：{expr} = {result}"
        except Exception as e:
            return f"计算错误：{e}"

    # ------------------------------------------------------------------
    # 三、搜索：双源降级，任一被反爬时另一个顶上
    # ------------------------------------------------------------------
    def search(self, query):
        # 主源：360 搜索移动版，中文长查询结果相关、反爬比 PC 版弱
        try:
            r = requests.get("https://m.so.com/s", params={"q": query},
                             headers=HEADERS, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            doc = html.fromstring(r.content)
            title = doc.xpath("//title")
            blocked = title and any(
                k in title[0].text_content() for k in ("访问异常", "安全验证", "验证码")
            )
            if not blocked:
                lines = []
                for block in doc.xpath("//div[contains(@class,'res-list')]"):
                    # 结果块里内嵌 <style>/<script>，先删掉再取文本，否则摘要会被 CSS 污染
                    for bad in block.xpath(".//script | .//style | .//noscript"):
                        bad.getparent().remove(bad)
                    h, a = block.xpath(".//h3"), block.xpath(".//a[@href]")
                    if not (h or a):
                        continue
                    t = " ".join((h[0] if h else a[0]).text_content().split())
                    link = a[0].get("href") if a else ""
                    # 过滤「相关推荐」「其他人还在搜」这类导航块
                    if not t or link.startswith("#") or link.startswith("/s?"):
                        continue
                    # 360 的结果是 jump?u=真实地址，还原后前端链接才可直接点开
                    if "m.so.com/jump" in link:
                        link = parse_qs(urlparse(link).query).get("u", [link])[0]
                    con = block.xpath(".//div[contains(@class,'res-con')]")
                    desc = " ".join(con[0].text_content().split())[:160] if con else ""
                    lines.append(f"{len(lines)+1}. {t}\n   链接：{link}\n   摘要：{desc}")
                    if len(lines) >= 4:
                        break
                if lines:
                    return "\n".join(lines)
        except Exception:
            pass

        # 兜底：Bing RSS。必须用 config.py 里的完整 UA，
        # 否则会返回「HTTP 200 + 合法 XML + 0 条 item」的假成功。
        try:
            r = requests.get("https://www.bing.com/search",
                             params={"q": query, "format": "rss"},
                             headers=HEADERS, timeout=HTTP_TIMEOUT)
            items = ET.fromstring(r.content).findall("./channel/item")
            if items:
                return "\n".join(
                    f"{i}. {(it.findtext('title') or '').strip()}\n"
                    f"   摘要：{re.sub(r'<[^>]+>', '', (it.findtext('description') or '')).strip()[:160]}"
                    for i, it in enumerate(items[:4], 1)
                )
        except Exception:
            pass

        return "搜索失败：搜索源暂时不可用（反爬或网络受限）"

    # ------------------------------------------------------------------
    # 四、练习三：待办事项
    # ------------------------------------------------------------------
    def add_todo(self, item):
        """添加一条待办。item 由大脑从用户原话里抽取，这里只做清洗和落库。"""
        item = (item or "").strip()
        if not item:
            return "待办内容不能为空"
        self.todo_list.append({"id": len(self.todo_list) + 1, "item": item, "done": False})
        return f"已添加待办 #{len(self.todo_list)}：{item}（当前共 {len(self.todo_list)} 项）"

    def show_todos(self, _=""):
        """列出全部待办。参数占位是因为分派表会统一传 instruction 进来。"""
        if not self.todo_list:
            return "待办列表为空"
        lines = [f"当前共 {len(self.todo_list)} 项待办："]
        for t in self.todo_list:
            mark = "已完成" if t["done"] else "未完成"
            lines.append(f"  {t['id']}. [{mark}] {t['item']}")
        return "\n".join(lines)

    def get_todos(self):
        """给 Web 端 /api/todos 读取用：返回副本，避免外部直接改内部列表。"""
        return [dict(t) for t in self.todo_list]
