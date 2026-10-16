"""
tests/test_api_endpoints.py

核心职责：
使用 FastAPI TestClient 测试认证拦截、账号配置接口、系统配置接口、日志接口以及对外网关接口。
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.account_manager import account_manager
from backend.settings_manager import settings_manager


@pytest.fixture
def client():
    # 保护已保存账号不被测试修改或清空
    original_accounts = [acc.model_copy(deep=True) for acc in account_manager._accounts]
    yield TestClient(app)
    account_manager._accounts = original_accounts
    account_manager.save()


def test_auth_status(client):
    res = client.get("/api/auth/status")
    assert res.status_code == 200
    assert "auth_required" in res.json()


def test_login_and_access(client):
    # 模拟开启密码保护
    settings_manager.update_settings({"panel_password": "super_secret_pwd"})
    try:
        # 未登录访问受保护接口应返回 401
        res = client.get("/api/accounts")
        assert res.status_code == 401

        # 密码错误应返回 401
        res = client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res.status_code == 401

        # 正确密码登录
        res = client.post("/api/auth/login", json={"password": "super_secret_pwd"})
        assert res.status_code == 200
        token = res.json()["token"]

        # 使用 Token 访问受保护接口
        res = client.get("/api/accounts", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
    finally:
        # 恢复免密配置
        settings_manager.update_settings({"panel_password": ""})


def test_add_and_list_accounts(client):
    # 添加一个账号
    payload = {
        "email": "test_dev@gmail.com",
        "raw_cookie_or_psid": "g.a8i_test_psid_1234567890",
        "psidts": "sidts_test_12345",
        "remark": "测试账号",
        "is_enabled": True
    }
    res = client.post("/api/accounts", json=payload)
    assert res.status_code == 200
    acc_id = res.json()["account"]["id"]

    # 查询脱敏列表
    res = client.get("/api/accounts")
    assert res.status_code == 200
    accounts = res.json()
    assert any(a["id"] == acc_id for a in accounts)

    # 验证凭据脱敏
    target = next(a for a in accounts if a["id"] == acc_id)
    assert "..." in target["secure_1psid_masked"]
    assert target["secure_1psid_masked"] != "g.a8i_test_psid_1234567890"

    # 清理删除账号
    del_res = client.delete(f"/api/accounts/{acc_id}")
    assert del_res.status_code == 200


def test_add_account_without_email_uses_remark(client):
    # 模拟用户输入：不填邮箱，仅填备注与 Cookie JSON
    user_cookie_json = {
        "__Secure-1PSID": "g.a000DAmwaBTuhBYwtd6ihZOYTEYptWEYJrz7eCdD2V-6TZXIxKF2ra9XHWEnSYU_9PaYfiINLAACgYKAUQSARQSFQHGX2MiQv8chFzkyUfzeZjophXLtxoVAUF8yKo_2xFZK_pESQbI4RgOwGQl0076",
        "__Secure-1PSIDTS": "sidts-CjcBkldj_0paOysHGMZ7LyZN4n2xWAlO2QV9gEowcH3EKxH9nsNi6n2b6RMsHS8A8K0FAgjxtyO6EAA"
    }
    payload = {
        "email": "",
        "remark": "li",
        "raw_cookie_or_psid": user_cookie_json,
        "is_enabled": True
    }
    res = client.post("/api/accounts", json=payload)
    assert res.status_code == 200
    account = res.json()["account"]
    assert account["email"] == "li"
    assert account["remark"] == "li"
    assert account["secure_1psid"] == user_cookie_json["__Secure-1PSID"]
    assert account["secure_1psidts"] == user_cookie_json["__Secure-1PSIDTS"]

    # 验证单账号详情查询接口 (用于编辑回显)
    detail_res = client.get(f"/api/accounts/{account['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["remark"] == "li"
    assert detail["secure_1psid"] == user_cookie_json["__Secure-1PSID"]
    assert detail["secure_1psidts"] == user_cookie_json["__Secure-1PSIDTS"]

    # 清理
    client.delete(f"/api/accounts/{account['id']}")


def test_settings_api(client):
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert "health_check_interval_minutes" in res.json()

    # 更新配置
    res = client.post("/api/settings", json={"health_check_interval_minutes": 20})
    assert res.status_code == 200
    assert res.json()["settings"]["health_check_interval_minutes"] == 20


def test_logs_api(client):
    res = client.get("/api/logs?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data


def test_frontend_static_serving(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Gemini Web Gateway" in res.text

    css_res = client.get("/static/style.css")
    assert css_res.status_code == 200

    js_res = client.get("/static/app.js")
    assert js_res.status_code == 200


def test_proxy_image_invalid_url(client):
    res = client.get("/api/proxy/image?url=invalid-protocol://abc")
    assert res.status_code == 400


def test_gateway_api_key_auth(client):
    settings_manager.update_settings({"api_key": "sk-gateway-secret-test"})
    try:
        # 未提供 API Key 应该 401
        res = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        assert res.status_code == 401

        # 错误 API Key 应该 401
        res = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-wrong-key"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 401
    finally:
        settings_manager.update_settings({"api_key": ""})


def test_image_management_endpoints(client):
    from backend.media_service import media_service
    # 1. 列表接口
    res = client.get("/api/images")
    assert res.status_code == 200
    data = res.json()
    assert "images" in data
    assert "total_count" in data

    # 2. 创建临时测试图片并测试删除与清空
    test_file = media_service.media_dir / "test_sample_img.jpg"
    test_file.write_bytes(b"test image content")

    try:
        res = client.get("/api/images")
        assert res.status_code == 200
        assert any(img["filename"] == "test_sample_img.jpg" for img in res.json()["images"])

        # 测试静态资源挂载可访问
        res_static = client.get("/media/images/test_sample_img.jpg")
        assert res_static.status_code == 200
        assert res_static.content == b"test image content"

        # 3. 单个删除接口
        res_del = client.delete("/api/images/test_sample_img.jpg")
        assert res_del.status_code == 200
        assert res_del.json()["success"] is True

        # 4. 删除不存在文件返回 404
        res_del_404 = client.delete("/api/images/non_existent.jpg")
        assert res_del_404.status_code == 404
    finally:
        if test_file.exists():
            test_file.unlink()


def test_account_health_logs_endpoint(client):
    accs = account_manager._accounts
    if not accs:
        return
    acc_id = accs[0].id

    # 1. 尝试拉取检查日志
    res = client.get(f"/api/accounts/{acc_id}/health-logs")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "items" in data
    assert "stats" in data
    assert "total" in data

    # 2. 模拟写入一条 health_check 日志并验证能查询到
    from backend.log_service import log_service
    import asyncio
    asyncio.run(
        log_service.add_log(
            operation="health_check",
            token_email=accs[0].remark or accs[0].email,
            status="已完成",
            progress="100%",
            status_code=200,
            summary="[单元测试] 巡检测试记录",
            duration=0.5,
        )
    )

    res2 = client.get(f"/api/accounts/{acc_id}/health-logs")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["total"] >= 1
    assert any("单元测试" in item.get("summary", "") for item in data2["items"])


def test_configurable_concurrency_and_least_connections(client):
    """测试单账号并发配置保存与最少活跃连接调度逻辑"""
    from backend.account_manager import account_manager

    # 1. 添加一个带自定义并发限制的账号
    res = client.post(
        "/api/accounts",
        json={
            "remark": "并发测试账号",
            "raw_cookie_or_psid": "g.a000testpsidconcurrency0001",
            "psidts": "sidts-test0001",
            "concurrency_limit": 5,
        },
    )
    assert res.status_code == 200
    acc_id = res.json()["account"]["id"]

    # 2. 获取列表验证 effective_concurrency 与 concurrency_limit
    res_list = client.get("/api/accounts")
    assert res_list.status_code == 200
    target = next((a for a in res_list.json() if a["id"] == acc_id), None)
    assert target is not None
    assert target["concurrency_limit"] == 5
    assert target["effective_concurrency"] == 5
    assert target["active_connections"] == 0

    # 3. 更新为 3
    res_update = client.put(f"/api/accounts/{acc_id}", json={"concurrency_limit": 3})
    assert res_update.status_code == 200
    assert account_manager.get_concurrency_limit(acc_id) == 3

    # 4. 验证活跃连接计数器与信号量动态更新
    account_manager.increment_active(acc_id)
    assert account_manager._active_connections.get(acc_id) == 1
    account_manager.decrement_active(acc_id)
    assert account_manager._active_connections.get(acc_id) == 0

    # 清理测试账号
    account_manager.delete_account(acc_id)


def test_guest_single_model_auto_expired(monkeypatch):
    """测试当 Google 仅返回 1 个模型（访客模式）或缺失 Pro/Flash 时，必须判定为 EXPIRED"""
    import asyncio
    from backend.account_manager import account_manager, AccountModel
    from gemini_webapi.constants import AccountStatus

    # 构造假 client
    class MockAvailableModel:
        def __init__(self, name):
            self.model_name = name
            self.is_available = True

    class MockGuestClient:
        account_status = AccountStatus.AVAILABLE
        quotas = {}
        usage_info = None
        cookies = None

        async def init(self, **kwargs):
            pass

        def list_models(self):
            # 模拟 Google 降级访客模式，只返回 1 个 flash-lite 模型
            return [MockAvailableModel("gemini-flash-lite")]

    test_acc = AccountModel(
        id="acc_test_guest",
        email="guest_test@example.com",
        remark="测试访客账号",
        secure_1psid="g.a000testpsid",
        secure_1psidts="sidts-test",
        is_enabled=True,
        status="ACTIVE",
    )
    account_manager._accounts.append(test_acc)

    async def mock_get_or_create(*args, **kwargs):
        return MockGuestClient()

    monkeypatch.setattr(account_manager, "get_or_create_client", mock_get_or_create)

    # 执行健康检查
    res = asyncio.run(account_manager.check_health("acc_test_guest", source="auto"))

    # 验证账号状态被严格判定为 EXPIRED
    assert res["status"] == "EXPIRED"
    assert test_acc.status == "EXPIRED"
    assert test_acc.is_enabled is False

    # 清理
    account_manager.delete_account("acc_test_guest")





