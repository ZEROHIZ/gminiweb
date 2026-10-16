"""
tests/test_auth.py

核心职责：
测试凭据脱敏算法、轻量 HMAC-SHA256 Token 签发与过期防伪校验。
"""

import time
from backend.auth import create_panel_token, mask_credential, verify_panel_token


def test_mask_credential():
    assert mask_credential("") == ""
    assert mask_credential("12345") == "***"
    assert mask_credential("g.a8i1234567890abcdef3x9a") == "g.a8i1...3x9a"


def test_panel_token_flow():
    token = create_panel_token()
    assert verify_panel_token(token) is True
    assert verify_panel_token("invalid.token") is False
    assert verify_panel_token("") is False
    assert verify_panel_token("12345") is False
