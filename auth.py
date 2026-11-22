"""
鉴权模块 auth.py —— API Key 的签发与校验
=========================================
这一层只做一件事：把「一串随机字符」变成「服务端认识的一张凭证」。

三个动作对应上一轮讲过的原理：
    签发 issue()  -> 生成随机串，把 SHA-256 哈希存进表，明文只返回一次
    携带          -> 由前端放在 Authorization: Bearer <key> 请求头里
    校验 verify() -> 把请求里的 key 再算一次哈希，查表比对

安全约定：
    * 磁盘上只存哈希，不存明文。文件泄露也无法直接拿来调用。
    * 用 hmac.compare_digest 做定长比较，避免普通字符串比较的时序差异。
    * ThreadingHTTPServer 是多线程的，读写表与写文件全部加锁。
    * 落盘用「临时文件 + 原子替换」，避免读到写了一半的 JSON。

注意：签发接口本身是开放的（按需求，任何能打开页面的设备都能自助申请），
      因此它实现的是「登记」而不是「授权」；若要做真正的授权，
      应给签发接口再加一道管理员口令，见 server.py 中对应注释。
"""
import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime
from pathlib import Path

from .config import API_KEY_PREFIX


class APIKeyStore:
    """API Key 哈希表：{sha256(key): {label, created_at}}。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._keys = {}
        self._load()

    # ------------------------------------------------------------------
    # 持久化：启动时读一次，每次签发后写一次
    # ------------------------------------------------------------------
    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._keys = data
        except (json.JSONDecodeError, OSError):
            # 文件损坏时从空表开始，不要因此让整个服务起不来
            self._keys = {}

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._keys, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    @staticmethod
    def _hash(key):
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def issue(self, label=""):
        """签发一个新 key。返回明文（整个生命周期里只有这一次能看到它）。"""
        key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        digest = self._hash(key)
        with self._lock:
            self._keys[digest] = {
                "label": str(label)[:40],
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save()
        return key

    def verify(self, key):
        """校验 key 是否在哈希表里；返回 bool，不泄露任何表内信息。"""
        if not key or not isinstance(key, str):
            return False
        digest = self._hash(key)
        with self._lock:
            for stored in self._keys:
                if hmac.compare_digest(stored, digest):
                    return True
        return False

    def count(self):
        with self._lock:
            return len(self._keys)
