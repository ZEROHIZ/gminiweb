"""
tests/test_log_service.py

核心职责：
测试请求日志管理器的 SQLite WAL 初始化、增删查、分页检索、状态过滤与按量滚动清理。
"""

import tempfile
from pathlib import Path
import pytest
from backend.log_service import LogService


@pytest.mark.asyncio
async def test_log_service_lifecycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        svc = LogService(data_dir=tmp_dir)
        await svc.init_db()

        # 写入一条成功日志
        log_id1 = await svc.add_log(
            operation="generate_image",
            token_email="test1@gmail.com",
            status="已完成",
            progress="100%",
            status_code=200,
            summary="图片结果已返回",
            duration=47.2,
            request_data={"model": "imagen-3.0", "prompt": "一只猫"},
            response_data={"data": [{"url": "http://img.com/cat.png"}]},
            result_url="http://img.com/cat.png",
        )
        assert log_id1 > 0

        # 写入一条失败日志
        log_id2 = await svc.add_log(
            operation="chat_completion",
            token_email="test2@gmail.com",
            status="失败",
            progress="0%",
            status_code=500,
            summary="生成失败",
            duration=1.2,
            error_reason="网络超时",
            error_response={"error": "timeout"},
        )
        assert log_id2 > log_id1

        # 测试分页获取
        logs_page = await svc.get_logs(page=1, page_size=10)
        assert logs_page["total"] == 2
        assert len(logs_page["items"]) == 2

        # 测试按状态过滤
        failed_logs = await svc.get_logs(page=1, page_size=10, status="失败")
        assert failed_logs["total"] == 1
        assert failed_logs["items"][0]["id"] == log_id2

        # 测试按操作过滤
        img_logs = await svc.get_logs(page=1, page_size=10, operation="generate_image")
        assert img_logs["total"] == 1
        assert img_logs["items"][0]["id"] == log_id1

        # 测试单条详情查询
        detail = await svc.get_log_by_id(log_id1)
        assert detail is not None
        assert detail["result_url"] == "http://img.com/cat.png"
        assert detail["request_data"] == {"model": "imagen-3.0", "prompt": "一只猫"}

        # 测试日志清理
        cleaned = await svc.cleanup_old_logs(max_entries=1, retention_days=30)
        assert cleaned >= 1
        after_clean = await svc.get_logs(page=1, page_size=10)
        assert after_clean["total"] == 1

        # 测试一键清空
        await svc.clear_logs()
        cleared = await svc.get_logs(page=1, page_size=10)
        assert cleared["total"] == 0
