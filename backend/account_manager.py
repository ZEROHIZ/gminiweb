"""
backend/account_manager.py

核心职责：
Gemini 多账号生命周期与账号池管理器。负责账号配置持久化（data/accounts.json）、
多格式 Cookie 智能解析导入、GeminiClient 实例维护与并发保护、动态配额与可用模型更新、
Cookie 自动轮换刷新监听与持久化回写，以及多账号轮询与故障转移调度。

主要功能：
1. parse_cookie_input: 兼容解析纯键值、包裹字典、浏览器插件 JSON 数组及 Cookie 标头。
2. AccountModel / PublicAccountModel: 账号数据模型与面向前端的敏感数据脱敏模型。
3. AccountManager: 核心单例，负责账号增删改查、初始化预热、信号量并发隔离、轮询调度与 Cookie 自动持久化回写。
"""

import asyncio
import datetime
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Tuple

from pydantic import BaseModel, Field
from gemini_webapi import GeminiClient
from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import AuthError, GeminiError

import time
from backend.auth import mask_credential
from backend.settings_manager import settings_manager
from backend.log_service import log_service


def parse_cookie_input(raw: Any) -> Tuple[str, str, float | None, str]:
    """
    智能解析多种格式的 Cookie 输入：
    1. 浏览器插件 (Cookie-Editor / EditThisCookie) 导出的 JSON 数组
    2. 扁平键值对字典：{"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}
    3. 嵌套字典：{"cookies": {...}}
    4. 标准 HTTP Cookie 字符串：__Secure-1PSID=...; __Secure-1PSIDTS=...
    返回：(secure_1psid, secure_1psidts, expiration_timestamp, extracted_email)
    """
    import re
    if isinstance(raw, str):
        raw_str = raw.strip()
        if (raw_str.startswith("{") and raw_str.endswith("}")) or (raw_str.startswith("[") and raw_str.endswith("]")):
            try:
                raw = json.loads(raw_str)
            except Exception:
                pass

    psid = ""
    psidts = ""
    exp_date = None
    extracted_email = ""

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("key") or ""
            val = str(item.get("value") or "").strip()
            if name == "__Secure-1PSID":
                psid = val
                exp_date = item.get("expirationDate") or item.get("expires") or exp_date
            elif name == "__Secure-1PSIDTS":
                psidts = val
                exp_date = item.get("expirationDate") or item.get("expires") or exp_date
            elif "email" in name.lower() or "user" in name.lower() or "@" in val:
                m = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", val)
                if m and not extracted_email:
                    extracted_email = m.group(0)

    elif isinstance(raw, dict):
        if "cookies" in raw:
            return parse_cookie_input(raw["cookies"])
        for k, v in raw.items():
            k_lower = str(k).lower()
            val = str(v).strip()
            if k == "__Secure-1PSID" or k_lower == "secure_1psid":
                psid = val
            elif k == "__Secure-1PSIDTS" or k_lower == "secure_1psidts":
                psidts = val
            elif "expir" in k_lower:
                try:
                    exp_date = float(val)
                except Exception:
                    pass
            elif "email" in k_lower or "account" in k_lower or "user" in k_lower:
                m = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", val)
                if m and not extracted_email:
                    extracted_email = m.group(0)

    elif isinstance(raw, str):
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "__Secure-1PSID":
                    psid = v
                elif k == "__Secure-1PSIDTS":
                    psidts = v
        m = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", raw)
        if m and not extracted_email:
            extracted_email = m.group(0)

    return psid, psidts, exp_date, extracted_email


def extract_auth_cookies(cookies: Any) -> Tuple[str | None, str | None]:
    """从 curl_cffi Cookies 提取最新的 __Secure-1PSID 与 __Secure-1PSIDTS"""
    psid = None
    psidts = None
    if hasattr(cookies, "jar"):
        for c in cookies.jar:
            name = getattr(c, "name", None)
            val = getattr(c, "value", None)
            if name == "__Secure-1PSID":
                psid = val
            elif name == "__Secure-1PSIDTS":
                psidts = val
    if hasattr(cookies, "get"):
        if not psid:
            psid = cookies.get("__Secure-1PSID")
        if not psidts:
            psidts = cookies.get("__Secure-1PSIDTS")
    return psid, psidts


class AccountModel(BaseModel):
    id: str = Field(default_factory=lambda: f"acc_{uuid.uuid4().hex[:8]}")
    email: str
    remark: str = ""
    secure_1psid: str
    secure_1psidts: str = ""
    proxy: str | None = None
    is_enabled: bool = True
    status: str = "ACTIVE"  # ACTIVE, EXPIRED, RESTRICTED, DISABLED
    last_checked_at: str | None = None
    models: list[str] = Field(default_factory=lambda: ["gemini-2.5-flash", "gemini-2.5-pro"])
    quota_info: dict = Field(default_factory=dict)
    expiration_date: float | None = None


class PublicAccountModel(BaseModel):
    id: str
    email: str
    remark: str
    secure_1psid_masked: str
    secure_1psidts_masked: str
    proxy: str | None
    is_enabled: bool
    status: str
    last_checked_at: str | None
    models: list[str]
    quota_info: dict
    expiration_date: float | None


class AccountManager:
    def __init__(self, data_dir: str | Path | None = None):
        base_dir = Path(data_dir or os.getenv("DATA_DIR", "data"))
        self.data_dir = base_dir
        self.file_path = base_dir / "accounts.json"
        self._accounts: list[AccountModel] = []
        self._clients: dict[str, GeminiClient] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._rr_index = 0
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._accounts = [AccountModel(**item) for item in data]
                        return
            except Exception:
                pass
        self._accounts = []
        self.save()

    def save(self) -> None:
        """原子落盘保存 accounts.json"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = [acc.model_dump() for acc in self._accounts]
        dir_path = self.file_path.parent
        with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            temp_name = tf.name
        os.replace(temp_name, self.file_path)

    def get_public_accounts(self) -> list[PublicAccountModel]:
        """返回已脱敏保护的账号列表供前端渲染"""
        results = []
        for acc in self._accounts:
            results.append(
                PublicAccountModel(
                    id=acc.id,
                    email=acc.email,
                    remark=acc.remark,
                    secure_1psid_masked=mask_credential(acc.secure_1psid),
                    secure_1psidts_masked=mask_credential(acc.secure_1psidts),
                    proxy=acc.proxy,
                    is_enabled=acc.is_enabled,
                    status=acc.status,
                    last_checked_at=acc.last_checked_at,
                    models=acc.models or ["gemini-2.5-flash", "gemini-2.5-pro"],
                    quota_info=acc.quota_info or {},
                    expiration_date=acc.expiration_date,
                )
            )
        return results

    def get_account(self, account_id: str) -> AccountModel | None:
        for acc in self._accounts:
            if acc.id == account_id:
                return acc
        return None

    def add_account(
        self,
        email: str = "",
        raw_cookie_or_psid: Any = None,
        psidts: str = "",
        remark: str = "",
        proxy: str | None = None,
        is_enabled: bool = True,
    ) -> AccountModel:
        """支持单项输入或浏览器插件格式输入直接添加账号"""
        parsed_psid, parsed_psidts, exp_date, extracted_email = parse_cookie_input(raw_cookie_or_psid)
        final_psid = parsed_psid or (str(raw_cookie_or_psid).strip() if raw_cookie_or_psid else "")
        final_psidts = parsed_psidts or psidts.strip()

        if not final_psid:
            raise ValueError("必须提供 __Secure-1PSID 凭据")

        clean_email = (email or "").strip()
        clean_remark = (remark or "").strip()

        # 邮箱非必填：优先从 Cookie 提取，其次使用备注，最后使用凭据后缀作为账号标识
        if not clean_email:
            if extracted_email:
                clean_email = extracted_email
            elif clean_remark:
                clean_email = clean_remark
            else:
                clean_email = f"账号_{final_psid[-6:]}"

        # 检查是否已存在同凭据或同标识账号（优先以凭据排重）
        for existing in self._accounts:
            if existing.secure_1psid == final_psid or (clean_email and existing.email.strip().lower() == clean_email.lower()):
                existing.email = clean_email
                existing.secure_1psid = final_psid
                existing.secure_1psidts = final_psidts or existing.secure_1psidts
                existing.remark = clean_remark or existing.remark
                existing.proxy = proxy.strip() if proxy and proxy.strip() else existing.proxy
                existing.is_enabled = is_enabled
                existing.status = "ACTIVE"
                if exp_date:
                    existing.expiration_date = exp_date
                self.save()
                self._dispose_client(existing.id)
                return existing

        new_acc = AccountModel(
            email=clean_email,
            remark=clean_remark,
            secure_1psid=final_psid,
            secure_1psidts=final_psidts,
            proxy=proxy.strip() if proxy and proxy.strip() else None,
            is_enabled=is_enabled,
            status="ACTIVE",
            expiration_date=exp_date,
        )
        self._accounts.append(new_acc)
        self.save()
        return new_acc

    def update_account(
        self,
        account_id: str,
        email: str | None = None,
        raw_cookie_or_psid: Any = None,
        psidts: str | None = None,
        remark: str | None = None,
        proxy: str | None = None,
        is_enabled: bool | None = None,
        status: str | None = None,
    ) -> AccountModel:
        acc = self.get_account(account_id)
        if not acc:
            raise ValueError(f"未找到 ID 为 {account_id} 的账号")

        if email is not None and email.strip():
            acc.email = email.strip()
        if remark is not None:
            acc.remark = remark.strip()
            # 若原本没有真实邮箱且当前以备注作为标识，同步更新标识
            if not acc.email or acc.email == acc.remark:
                acc.email = remark.strip()
        if proxy is not None:
            acc.proxy = proxy.strip() if proxy.strip() else None
        if is_enabled is not None:
            acc.is_enabled = is_enabled
        if status is not None:
            acc.status = status

        if raw_cookie_or_psid:
            parsed_psid, parsed_psidts, exp_date, extracted_email = parse_cookie_input(raw_cookie_or_psid)
            if parsed_psid:
                acc.secure_1psid = parsed_psid
            if parsed_psidts:
                acc.secure_1psidts = parsed_psidts
            elif psidts is not None:
                acc.secure_1psidts = psidts.strip()
            if exp_date:
                acc.expiration_date = exp_date
            if extracted_email and (not acc.email or acc.email.startswith("账号_")):
                acc.email = extracted_email
            self._dispose_client(account_id)

        self.save()
        return acc

    def delete_account(self, account_id: str) -> bool:
        acc = self.get_account(account_id)
        if not acc:
            return False
        self._dispose_client(account_id)
        self._accounts = [a for a in self._accounts if a.id != account_id]
        self.save()
        return True

    def _dispose_client(self, account_id: str) -> None:
        """安全释放账号对应的底层连接"""
        client = self._clients.pop(account_id, None)
        self._semaphores.pop(account_id, None)
        if client:
            try:
                asyncio.create_task(client.close())
            except Exception:
                pass

    def get_semaphore(self, account_id: str) -> asyncio.Semaphore:
        """获取单账号并发信号量（默认并发度为 2）"""
        if account_id not in self._semaphores:
            self._semaphores[account_id] = asyncio.Semaphore(2)
        return self._semaphores[account_id]

    async def get_or_create_client(self, account_id: str, timeout: float | None = None) -> GeminiClient:
        """获取或创建 GeminiClient 实例"""
        acc = self.get_account(account_id)
        if not acc:
            raise ValueError(f"账号不存在: {account_id}")

        if account_id in self._clients:
            return self._clients[account_id]

        settings = settings_manager.get_settings()
        req_timeout = timeout or float(settings.timeout_seconds)
        effective_proxy = acc.proxy or settings.global_proxy or None

        client = GeminiClient(
            secure_1psid=acc.secure_1psid,
            secure_1psidts=acc.secure_1psidts or None,
            proxy=effective_proxy,
        )

        try:
            await client.init(timeout=req_timeout, auto_refresh=True)
            self._clients[account_id] = client
            # 初始化成功后立刻提取并持久化可能自动刷新的 Cookie
            self.persist_cookies_if_changed(account_id, client)
            return client
        except Exception as e:
            # 初始化失败记录状态
            if isinstance(e, AuthError) or "UNAUTHENTICATED" in str(e).upper():
                acc.status = "EXPIRED"
                if settings.auto_disable_expired:
                    acc.is_enabled = False
                self.save()
            raise

    def persist_cookies_if_changed(self, account_id: str, client: GeminiClient | None = None) -> bool:
        """
        核心机制：检查底层 client.cookies 中是否有最新的 __Secure-1PSID / __Secure-1PSIDTS，
        若检测到值变化，原子回写更新 data/accounts.json。
        """
        acc = self.get_account(account_id)
        if not acc:
            return False

        cli = client or self._clients.get(account_id)
        if not cli or not getattr(cli, "cookies", None):
            return False

        latest_psid, latest_psidts = extract_auth_cookies(cli.cookies)
        changed = False

        if latest_psid and latest_psid != acc.secure_1psid:
            acc.secure_1psid = latest_psid
            changed = True

        if latest_psidts and latest_psidts != acc.secure_1psidts:
            acc.secure_1psidts = latest_psidts
            changed = True

        if changed:
            self.save()
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    token_label = acc.remark or acc.email or acc.id
                    loop.create_task(
                        log_service.add_log(
                            operation="keep_alive",
                            token_email=token_label,
                            status="已完成",
                            progress="100%",
                            status_code=200,
                            summary="Google 会话 Cookie 自动轮换刷新成功，已原子回写持久化",
                            duration=0.01,
                            request_data={"account_id": acc.id, "remark": acc.remark},
                            response_data={
                                "psid_masked": f"{acc.secure_1psid[:6]}...{acc.secure_1psid[-4:]}" if acc.secure_1psid else "",
                                "psidts_masked": f"{acc.secure_1psidts[:6]}...{acc.secure_1psidts[-4:]}" if acc.secure_1psidts else "",
                                "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            },
                        )
                    )
            except RuntimeError:
                pass
            return True
        return False

    async def check_health(self, account_id: str, source: str = "auto") -> dict:
        """对指定账号执行单次健康检查、配额提取与模型刷新并记录日志"""
        acc = self.get_account(account_id)
        if not acc:
            return {"status": "ERROR", "message": "账号不存在"}

        start_time = time.time()
        settings = settings_manager.get_settings()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        acc.last_checked_at = now_str

        try:
            client = await self.get_or_create_client(account_id)

            # 刷新可用模型
            try:
                models = client.list_models()
                if models:
                    model_names = [m.model_name for m in models if getattr(m, "is_available", True)]
                    if model_names:
                        acc.models = model_names
            except Exception:
                pass

            # 动态解析配额信息
            quota_dict = {
                "flash": {"status": "充裕", "remaining": "100%", "reset_at": "正常"},
                "pro": {"status": "充裕", "remaining": "100%", "reset_at": "正常"},
                "compute_units": {"status": "正常", "has_hard_block": False},
            }

            try:
                quotas = getattr(client, "quotas", {}) or {}
                if "FLASH" in quotas:
                    q_flash = quotas["FLASH"]
                    quota_dict["flash"]["remaining"] = f"{q_flash.get('remaining', 100)}%"
                    if q_flash.get("remaining", 100) < 20:
                        quota_dict["flash"]["status"] = "中等"
                if "PRO" in quotas:
                    q_pro = quotas["PRO"]
                    quota_dict["pro"]["remaining"] = f"{q_pro.get('remaining', 100)}%"
                    if q_pro.get("remaining", 100) < 20:
                        quota_dict["pro"]["status"] = "受限"

                usage_info = getattr(client, "usage_info", {}) or {}
                if usage_info:
                    quota_dict["usage_info"] = usage_info
            except Exception:
                pass

            acc.quota_info = quota_dict

            # 校验账号底层状态
            account_status = getattr(client, "account_status", AccountStatus.AVAILABLE)
            if account_status == AccountStatus.AVAILABLE:
                acc.status = "ACTIVE"
            elif account_status == AccountStatus.UNAUTHENTICATED:
                acc.status = "EXPIRED"
                if settings.auto_disable_expired:
                    acc.is_enabled = False
            else:
                acc.status = "RESTRICTED"

            # 持久化回写轮换 Cookie
            self.persist_cookies_if_changed(account_id, client)
            self.save()

            # 记录巡检成功日志
            duration = round(time.time() - start_time, 2)
            summary_source = "后台自动巡检" if source == "auto" else "手动触发检测"
            summary = f"[{summary_source}] 账号状态正常 | 配额正常 | 可用模型: {len(acc.models)}"
            try:
                await log_service.add_log(
                    operation="health_check",
                    token_email=acc.remark or acc.email or acc.id,
                    status="已完成" if acc.status == "ACTIVE" else acc.status,
                    progress="100%",
                    status_code=200,
                    summary=summary,
                    duration=duration,
                    request_data={"account_id": acc.id, "remark": acc.remark, "source": source},
                    response_data={"status": acc.status, "models": acc.models, "quota": acc.quota_info},
                )
            except Exception:
                pass

            return {"status": acc.status, "models": acc.models, "quota": acc.quota_info}

        except Exception as e:
            err_msg = str(e)
            if isinstance(e, AuthError) or "UNAUTHENTICATED" in err_msg.upper():
                acc.status = "EXPIRED"
                if settings.auto_disable_expired:
                    acc.is_enabled = False
            else:
                acc.status = "RESTRICTED"
            self.save()

            # 记录巡检异常日志
            duration = round(time.time() - start_time, 2)
            summary_source = "后台自动巡检" if source == "auto" else "手动触发检测"
            summary = f"[{summary_source}] 巡检异常: {err_msg[:60]}"
            try:
                await log_service.add_log(
                    operation="health_check",
                    token_email=acc.remark or acc.email or acc.id,
                    status="失败",
                    progress="0%",
                    status_code=401 if acc.status == "EXPIRED" else 500,
                    summary=summary,
                    duration=duration,
                    request_data={"account_id": acc.id, "remark": acc.remark, "source": source},
                    error_reason=err_msg,
                )
            except Exception:
                pass

            return {"status": acc.status, "error": err_msg}

    async def check_all_health(self, concurrency: int = 5) -> dict:
        """并发限制巡检所有可用账号"""
        sem = asyncio.Semaphore(concurrency)

        async def _check_one(acc_id: str):
            async with sem:
                return acc_id, await self.check_health(acc_id)

        tasks = [_check_one(acc.id) for acc in self._accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        res_dict = {}
        for item in results:
            if isinstance(item, tuple):
                res_dict[item[0]] = item[1]
        return res_dict

    async def acquire_account(self, preferred_id: str | None = None) -> Tuple[AccountModel, GeminiClient, asyncio.Semaphore]:
        """
        从账号池调度可用账号：
        1. 若指定 preferred_id，优先使用该账号；
        2. 否则使用 Round-Robin 轮询调度处于启用且健康的账号；
        3. 返回 (AccountModel, GeminiClient, Semaphore)。
        """
        async with self._lock:
            if preferred_id:
                acc = self.get_account(preferred_id)
                if not acc or not acc.is_enabled:
                    raise GeminiError(f"指定的账号不存在或已被禁用: {preferred_id}")
                client = await self.get_or_create_client(acc.id)
                return acc, client, self.get_semaphore(acc.id)

            # 筛选所有启用账号
            enabled_accounts = [a for a in self._accounts if a.is_enabled and a.status != "EXPIRED"]
            if not enabled_accounts:
                raise GeminiError("没有可用的 Token 进行请求。所有 Token 都处于禁用或失效状态。")

            # 轮询调度
            count = len(enabled_accounts)
            for _ in range(count):
                self._rr_index = (self._rr_index + 1) % count
                selected = enabled_accounts[self._rr_index]
                try:
                    client = await self.get_or_create_client(selected.id)
                    return selected, client, self.get_semaphore(selected.id)
                except Exception:
                    continue

            # 若轮询失败，尝试首个可用
            selected = enabled_accounts[0]
            client = await self.get_or_create_client(selected.id)
            return selected, client, self.get_semaphore(selected.id)


# 全局单例
account_manager = AccountManager()
