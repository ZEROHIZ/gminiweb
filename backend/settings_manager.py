"""
backend/settings_manager.py

核心职责：
管理系统的全局配置数据（持久化于 data/settings.json），负责配置的加载、合并环境变量默认值、
动态更新与原子落盘，为健康巡检、安全鉴权、代理网关及日志清理提供统一的配置支撑。

主要功能：
1. SettingsModel: 系统配置数据结构校验（含检查周期、超时、代理、面板密码、API Key、日志保留策略）。
2. SettingsManager: 单例管理类，提供 get_settings() 与 update_settings() 接口，支持原子写入。
"""

import json
import os
import tempfile
from pathlib import Path
from pydantic import BaseModel, Field


class SettingsModel(BaseModel):
    health_check_interval_minutes: int = Field(default=30, ge=1, le=1440, description="账号有效性自动检查周期(分钟)")
    auto_disable_expired: bool = Field(default=True, description="Cookie失效时是否自动禁用账号")
    failover_retry: bool = Field(default=True, description="请求失败或受限时是否自动重试下一账号")
    timeout_seconds: int = Field(default=30, ge=5, le=300, description="单个请求超时时间(秒)")
    global_proxy: str = Field(default="", description="全局网络代理地址")
    panel_password: str = Field(default="", description="管理面板访问密码，留空则免密")
    api_key: str = Field(default="", description="对外 API 访问密钥，留空则免密")
    max_log_entries: int = Field(default=20000, ge=100, le=500000, description="最大保留日志条数")
    log_retention_days: int = Field(default=30, ge=1, le=365, description="日志保留最长天数")


class SettingsManager:
    def __init__(self, data_dir: str | Path | None = None):
        base_dir = Path(data_dir or os.getenv("DATA_DIR", "data"))
        self.data_dir = base_dir
        self.file_path = base_dir / "settings.json"
        self._settings: SettingsModel = SettingsModel()
        self._load()

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._settings = SettingsModel(**data)
                    return
            except Exception:
                pass

        # 若文件不存在或损坏，使用默认值并读取环境变量
        env_password = os.getenv("PANEL_PASSWORD", "")
        env_api_key = os.getenv("API_KEY", "")
        env_proxy = os.getenv("GLOBAL_PROXY", "")
        self._settings = SettingsModel(
            panel_password=env_password,
            api_key=env_api_key,
            global_proxy=env_proxy
        )
        self.save()

    def get_settings(self) -> SettingsModel:
        return self._settings.model_copy()

    def update_settings(self, new_data: dict) -> SettingsModel:
        current_dict = self._settings.model_dump()
        current_dict.update({k: v for k, v in new_data.items() if v is not None})
        validated = SettingsModel(**current_dict)
        self._settings = validated
        self.save()
        return self._settings.model_copy()

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = self._settings.model_dump()
        dir_path = self.file_path.parent
        # 原子写入避免并发截断
        with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            temp_name = tf.name
        os.replace(temp_name, self.file_path)


# 全局单例
settings_manager = SettingsManager()
