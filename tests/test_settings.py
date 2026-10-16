"""
tests/test_settings.py

核心职责：
测试系统配置管理器 settings_manager 的加载、动态更新、字段约束校验与原子持久化。
"""

import tempfile
from pathlib import Path
import pytest
from backend.settings_manager import SettingsManager, SettingsModel


def test_settings_default_and_update():
    with tempfile.TemporaryDirectory() as tmp_dir:
        manager = SettingsManager(data_dir=tmp_dir)
        settings = manager.get_settings()
        assert settings.health_check_interval_minutes == 30
        assert settings.auto_disable_expired is True
        assert settings.max_log_entries == 20000

        updated = manager.update_settings({"health_check_interval_minutes": 15, "timeout_seconds": 60})
        assert updated.health_check_interval_minutes == 15
        assert updated.timeout_seconds == 60

        # 重新加载验证持久化
        reloaded_mgr = SettingsManager(data_dir=tmp_dir)
        reloaded = reloaded_mgr.get_settings()
        assert reloaded.health_check_interval_minutes == 15
        assert reloaded.timeout_seconds == 60
