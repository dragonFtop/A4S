"""P4 Task 19 / AC-19：服务端状态三态（disabled / online / offline）+ 共享看板数据契约。

前端徽标与共享页消费的就是这两块数据；这里验证后端契约稳定、三态都正确。
（viewer 只读与写接口 403 由 smoke_share.py 覆盖。）
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_tmp = tempfile.mkdtemp(prefix="a4s_p4_ui_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'ui.db'}"
os.environ.setdefault("APP_ROLE", "client")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from shared.config import settings  # noqa: E402
from client import server_link  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {detail}")


class _OK(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *_):  # noqa: ANN002
        pass


def main() -> int:
    orig_url = settings.server_url
    orig_timeout = settings.server_status_timeout

    print("== 三态判定 ==")
    settings.server_url = ""
    st = server_link.status(force=True)
    check("未配置 → disabled", st.state == "disabled" and st.online is False, st.state)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _OK)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        settings.server_url = f"http://127.0.0.1:{port}"
        st = server_link.status(force=True)
        check("可连通 → online", st.state == "online" and st.online is True, st.state)
        check("online 带延迟", st.latency_ms is not None, str(st.latency_ms))
    finally:
        srv.shutdown()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    settings.server_url = f"http://127.0.0.1:{dead_port}"
    settings.server_status_timeout = 0.5
    st = server_link.status(force=True)
    check("连不上 → offline 且不挂起", st.state == "offline" and st.online is False, st.state)

    settings.server_url = orig_url
    settings.server_status_timeout = orig_timeout

    print("== HTTP 接口形状（前端徽标数据源） ==")
    with TestClient(app) as client:
        r = client.get("/api/server/status?force=true")
        body = r.json()
        check("GET /api/server/status 200", r.status_code == 200, str(r.status_code))
        check("含 state/online/url/latency_ms",
              {"state", "online", "url", "latency_ms"} <= set(body.keys()), str(body.keys()))
        check("disabled 时 state=disabled", body["state"] == "disabled", str(body["state"]))

        print("== 共享看板形状（前端共享页数据源） ==")
        reg = client.post("/api/auth/register", json={"username": "uiuser", "password": "secret123"})
        token = reg.json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        board = client.get("/api/shares", headers=h).json()
        check("GET /api/shares 含 granted/incoming/outgoing",
              {"granted", "incoming", "outgoing"} <= set(board.keys()), str(board.keys()))
        check("初始三组均为列表", all(isinstance(board[k], list) for k in ("granted", "incoming", "outgoing")))

    print(f"\n==== 通过 {PASSED} 项，失败 {FAILED} 项 ====")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
