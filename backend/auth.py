"""
backend/auth.py

核心职责：
安全鉴权中心。负责管理面板访问权限控制、对外 API 网关密钥校验以及敏感凭据脱敏保护。

主要功能：
1. mask_credential: 对 Cookie / Token 等敏感凭据实施掩码脱敏（保留前 6 位和后 4 位）。
2. create_panel_token / verify_panel_token: 基于 HMAC-SHA256 的自签名轻量令牌生成与校验。
3. verify_panel_auth: FastAPI 依赖项，针对 /api/* 接口进行面板管理员身份鉴权。
4. verify_api_key: FastAPI 依赖项，针对 /v1/* 外部网关接口进行 API Key 鉴权。
"""

import hashlib
import hmac
import time
import secrets
from fastapi import Header, HTTPException, Request, status
from backend.settings_manager import settings_manager

# 进程内签名密钥（容器重启自动重置，增强安全性）
_SECRET_KEY = secrets.token_bytes(32)


def mask_credential(val: str | None) -> str:
    """对敏感凭据进行脱敏遮蔽处理"""
    if not val:
        return ""
    val_str = str(val).strip()
    if len(val_str) <= 10:
        return "***"
    return f"{val_str[:6]}...{val_str[-4:]}"


def create_panel_token() -> str:
    """生成带有时间戳签名的访问令牌"""
    ts = str(int(time.time()))
    signature = hmac.new(_SECRET_KEY, ts.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ts}.{signature}"


def verify_panel_token(token: str) -> bool:
    """验证面板访问令牌的合法性与时效（默认 7 天有效）"""
    if not token or "." not in token:
        return False
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False
    ts, signature = parts
    try:
        ts_int = int(ts)
        # 7 天过期
        if time.time() - ts_int > 86400 * 7:
            return False
    except ValueError:
        return False

    expected_sig = hmac.new(_SECRET_KEY, ts.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)


async def verify_panel_auth(request: Request, authorization: str | None = Header(None)) -> bool:
    """
    面板接口鉴权依赖项：
    若未设置 panel_password，免密直接放行；
    若设置了 panel_password，校验 Authorization Bearer token 或 Cookie 中的 panel_token。
    """
    settings = settings_manager.get_settings()
    if not settings.panel_password:
        return True

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif "panel_token" in request.cookies:
        token = request.cookies.get("panel_token")

    if token and verify_panel_token(token):
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未授权访问或登录会话已过期，请输入面板密码",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_api_key(authorization: str | None = Header(None)) -> bool:
    """
    对外 API 网关接口鉴权依赖项：
    若未设置 api_key，允许调用；
    若设置了 api_key，必须在 Authorization Header 中提供正确的 Bearer Token。
    """
    settings = settings_manager.get_settings()
    if not settings.api_key:
        return True

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if token and hmac.compare_digest(token, settings.api_key):
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key",
        headers={"WWW-Authenticate": "Bearer"},
    )
