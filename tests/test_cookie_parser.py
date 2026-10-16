"""
tests/test_cookie_parser.py

核心职责：
测试多种浏览器导出格式与手动输入格式的 Cookie 智能提取解析（含邮箱自动提取）。
"""

import json
from backend.account_manager import parse_cookie_input


def test_parse_cookie_editor_array():
    sample = [
        {"name": "NID", "value": "123", "domain": ".google.com"},
        {"name": "__Secure-1PSID", "value": "psid_val_12345", "expirationDate": 1890000000.0},
        {"name": "__Secure-1PSIDTS", "value": "sidts_val_abcde", "expirationDate": 1890000000.0},
        {"name": "ACCOUNT_CHOOSER", "value": "testuser@gmail.com"},
    ]
    psid, psidts, exp, email = parse_cookie_input(sample)
    assert psid == "psid_val_12345"
    assert psidts == "sidts_val_abcde"
    assert exp == 1890000000.0
    assert email == "testuser@gmail.com"


def test_parse_flat_dict():
    sample = {
        "__Secure-1PSID": "psid_flat",
        "__Secure-1PSIDTS": "sidts_flat",
    }
    psid, psidts, _, email = parse_cookie_input(sample)
    assert psid == "psid_flat"
    assert psidts == "sidts_flat"
    assert email == ""


def test_parse_nested_dict():
    sample = {
        "cookies": {
            "__Secure-1PSID": "psid_nested",
            "__Secure-1PSIDTS": "sidts_nested",
        }
    }
    psid, psidts, _, email = parse_cookie_input(sample)
    assert psid == "psid_nested"
    assert psidts == "sidts_nested"
    assert email == ""


def test_parse_raw_header_string():
    raw_str = "SID=foo; __Secure-1PSID=psid_raw_val; __Secure-1PSIDTS=sidts_raw_val; HSID=bar"
    psid, psidts, _, email = parse_cookie_input(raw_str)
    assert psid == "psid_raw_val"
    assert psidts == "sidts_raw_val"
    assert email == ""
