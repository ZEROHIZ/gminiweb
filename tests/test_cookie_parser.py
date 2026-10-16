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


def test_parse_multiple_cookies_with_expiry_order():
    """测试当导出的数组中新旧两枚 __Secure-1PSIDTS 并存时，系统能够智能选出最新且有效期最长的一枚"""
    import time
    now = time.time()
    sample = [
        # 旧的已经过期
        {"name": "__Secure-1PSIDTS", "value": "expired_old_val", "expirationDate": now - 3600, "domain": ".google.com"},
        # 最新的、未过期的 (1年后到期)
        {"name": "__Secure-1PSIDTS", "value": "fresh_new_val_2027", "expirationDate": now + 86400 * 365, "domain": ".google.com"},
        # 旧的、快过期的 (排在后面，防止简单的遍历后写覆盖)
        {"name": "__Secure-1PSIDTS", "value": "older_val_soon_expired", "expirationDate": now + 1800, "domain": "google.com"},
        {"name": "__Secure-1PSID", "value": "psid_val_good", "expirationDate": now + 86400 * 365, "domain": ".google.com"},
    ]
    psid, psidts, exp, _ = parse_cookie_input(sample)
    assert psid == "psid_val_good"
    assert psidts == "fresh_new_val_2027"
    assert exp == now + 86400 * 365


def test_extract_auth_cookies_multiple_in_jar():
    """测试底层的 CookieJar 在 Google 轮换期间并存两个 PSIDTS 时，能够准确抓取最新凭据"""
    import time
    from http.cookiejar import Cookie, CookieJar
    from curl_cffi.requests import Cookies
    from backend.account_manager import extract_auth_cookies

    now = time.time()
    jar = CookieJar()
    # 旧 Cookie
    c_old = Cookie(
        version=0, name="__Secure-1PSIDTS", value="old_id_111", port=None, port_specified=False,
        domain="google.com", domain_specified=True, domain_initial_dot=False, path="/",
        path_specified=True, secure=True, expires=now + 100, discard=False, comment=None, comment_url=None, rest={}
    )
    # 新 Cookie
    c_new = Cookie(
        version=0, name="__Secure-1PSIDTS", value="new_fresh_id_222", port=None, port_specified=False,
        domain=".google.com", domain_specified=True, domain_initial_dot=True, path="/",
        path_specified=True, secure=True, expires=now + 86400 * 30, discard=False, comment=None, comment_url=None, rest={}
    )
    # PSID
    c_psid = Cookie(
        version=0, name="__Secure-1PSID", value="psid_333", port=None, port_specified=False,
        domain=".google.com", domain_specified=True, domain_initial_dot=True, path="/",
        path_specified=True, secure=True, expires=now + 86400 * 30, discard=False, comment=None, comment_url=None, rest={}
    )
    jar.set_cookie(c_new)
    jar.set_cookie(c_old)
    jar.set_cookie(c_psid)

    cookies = Cookies()
    cookies.jar = jar

    psid, psidts = extract_auth_cookies(cookies)
    assert psid == "psid_333"
    assert psidts == "new_fresh_id_222"
