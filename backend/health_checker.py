"""
backend/health_checker.py

核心职责：
后台动态健康巡检器。负责常驻定时并发巡检所有 Gemini 账号状态、动态更新配额与模型、
持久化回写轮换 Cookie，以及在巡检完成后执行日志自动滚动清理。
支持通过 asyncio.Event 动态重置周期，用户在前端修改巡检间隔无需重启服务即可立即生效。

主要功能：
1. HealthChecker: 动态定时任务调度器。
2. trigger_check_now: 外部主动唤醒立即执行全量健康巡检。
3. notify_interval_changed: 通知调度器巡检周期已更改，立即重置等待计时。
4. run_loop: 核心调度循环，结合 asyncio.wait_for 与 Event 响应动态周期。
"""

import asyncio
from loguru import logger
from backend.account_manager import account_manager
from backend.log_service import log_service
from backend.settings_manager import settings_manager


class HealthChecker:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="gemini_health_checker")
        logger.info("健康巡检调度器已启动")

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("健康巡检调度器已停止")

    def trigger_check_now(self) -> None:
        """立即唤醒巡检任务"""
        self._wake_event.set()

    def notify_interval_changed(self) -> None:
        """巡检间隔被前端修改时唤醒重置等待时间"""
        self._wake_event.set()

    async def _execute_check_and_cleanup(self) -> None:
        """执行全量账号巡检与轻量日志清理"""
        try:
            logger.info("开始执行全量账号健康巡检...")
            check_results = await account_manager.check_all_health(concurrency=5)
            logger.info(f"账号健康巡检完成，涉及账号数: {len(check_results)}")

            # 巡检完成后自动清理日志
            settings = settings_manager.get_settings()
            cleaned = await log_service.cleanup_old_logs(
                max_entries=settings.max_log_entries,
                retention_days=settings.log_retention_days,
            )
            if cleaned > 0:
                logger.info(f"自动日志滚动清理完成，清理旧日志条数: {cleaned}")
        except Exception as e:
            logger.error(f"健康巡检或日志清理执行异常: {e}")

    async def _run_loop(self) -> None:
        # 冷启动后等待 5 秒，让 Web 服务优先就绪
        await asyncio.sleep(5)
        # 首次启动执行一次初检
        await self._execute_check_and_cleanup()

        while self._running:
            try:
                settings = settings_manager.get_settings()
                interval_seconds = max(60, settings.health_check_interval_minutes * 60)
                self._wake_event.clear()

                try:
                    # 等待周期结束或被外部事件即刻唤醒
                    await asyncio.wait_for(self._wake_event.wait(), timeout=interval_seconds)
                    logger.info("健康巡检器接收到即刻触发/周期更新唤醒信号")
                except asyncio.TimeoutError:
                    pass

                if not self._running:
                    break

                await self._execute_check_and_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康巡检循环异常: {e}")
                await asyncio.sleep(10)


# 全局单例
health_checker = HealthChecker()
