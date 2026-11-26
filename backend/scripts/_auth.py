"""冒烟脚本共用的登录辅助。

加了账号体系之后，所有接口都要带 token。每个脚本自己写一遍"注册→失败则登录"
既啰嗦又容易不一致，集中在这里。

每个冒烟脚本跑在自己的临时库上，所以注册一定能成功；但为了能反复跑同一个库，
仍然保留"已存在就登录"的兜底。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# 固定账号：临时库每次重建，不需要随机
_USERNAME = "smoke"
_PASSWORD = "smoke-test-123"


def auth_headers(client: TestClient) -> dict[str, str]:
    """注册（或登录）测试账号，返回可直接塞进 client.headers 的字典。"""
    client.headers.pop("Authorization", None)
    resp = client.post(
        "/api/auth/register",
        json={"username": _USERNAME, "password": _PASSWORD, "display_name": "冒烟"},
    )
    if resp.status_code == 400:
        resp = client.post(
            "/api/auth/login", json={"username": _USERNAME, "password": _PASSWORD}
        )
    resp.raise_for_status()
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient) -> None:
    """给 client 挂上认证头。之后所有请求自动带 token。"""
    client.headers.update(auth_headers(client))


def sign_in(client: TestClient, username: str, password: str) -> str:
    """再登一个账号（专门用来验证跨空间隔离）。返回它的 token。"""
    client.headers.pop("Authorization", None)
    resp = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    if resp.status_code == 400:
        resp = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
    resp.raise_for_status()
    return resp.json()["token"]
