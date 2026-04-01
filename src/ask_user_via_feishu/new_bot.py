#!/usr/bin/env python3
"""One-click Feishu/Lark bot creation via OAuth2 Device Flow.

Usage:
    python -m ask_user_via_feishu.new_bot
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    import qrcode  # type: ignore[import-untyped]
except ImportError:
    qrcode = None  # type: ignore[assignment]

ENDPOINTS = {
    "feishu": {
        "accounts": "https://accounts.feishu.cn",
        "open": "https://open.feishu.cn",
    },
    "lark": {
        "accounts": "https://accounts.larksuite.com",
        "open": "https://open.larksuite.com",
    },
}

MAX_POLL_ATTEMPTS = 200
MAX_POLL_INTERVAL = 60


class AppCreationError(Exception):
    """Raised when the Feishu app creation flow fails."""


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise AppCreationError(f"HTTP {exc.code}: {raw[:200]}") from exc
        error_msg = body.get("error_description") or body.get("error") or raw[:200]
        raise AppCreationError(f"HTTP {exc.code}: {error_msg}") from exc
    if not isinstance(body, dict):
        raise AppCreationError("Unexpected response format.")
    return body


def begin_registration() -> dict[str, Any]:
    url = f"{ENDPOINTS['feishu']['accounts']}/oauth/v1/app/registration"
    data = _post_form(url, {
        "action": "begin",
        "archetype": "PersonalAgent",
        "auth_method": "client_secret",
        "request_user_info": "open_id tenant_brand",
    })
    if "error" in data:
        msg = data.get("error_description") or data.get("error") or "Unknown error"
        raise AppCreationError(f"注册失败: {msg}")
    device_code = str(data.get("device_code") or "").strip()
    user_code = str(data.get("user_code") or "").strip()
    if not device_code or not user_code:
        raise AppCreationError("获取 device_code/user_code 失败")
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": str(data.get("verification_uri") or "").strip(),
        "expires_in": int(data.get("expires_in") or 300),
        "interval": int(data.get("interval") or 5),
    }


def poll_registration(
    brand: str,
    device_code: str,
    interval: int,
    expires_in: int,
) -> dict[str, Any]:
    url = f"{ENDPOINTS[brand]['accounts']}/oauth/v1/app/registration"
    deadline = time.time() + expires_in
    current_interval = interval

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        if time.time() >= deadline:
            break
        time.sleep(current_interval)

        try:
            data = _post_form(url, {"action": "poll", "device_code": device_code})
        except AppCreationError:
            current_interval = min(current_interval + 1, MAX_POLL_INTERVAL)
            continue

        err = str(data.get("error") or "").strip()

        if not err and data.get("client_id"):
            return {
                "client_id": str(data["client_id"]),
                "client_secret": str(data.get("client_secret") or ""),
                "user_info": data.get("user_info") or {},
            }

        if err == "authorization_pending":
            if attempt % 10 == 0:
                print(f"  等待扫码确认中... (第{attempt}次)")
            continue
        if err == "slow_down":
            current_interval = min(current_interval + 5, MAX_POLL_INTERVAL)
            continue
        if err == "access_denied":
            raise AppCreationError("用户拒绝了授权")
        if err in ("expired_token", "invalid_grant"):
            raise AppCreationError("device_code 已过期，请重试")
        if err:
            desc = data.get("error_description") or err
            raise AppCreationError(f"请求失败: {desc}")

    raise AppCreationError("请求超时，请重试")


def show_qr(user_code: str, brand: str = "feishu") -> str:
    full_url = f"{ENDPOINTS[brand]['open']}/page/cli?user_code={user_code}"
    print(f"\n验证链接: {full_url}\n")

    if qrcode is not None:
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(full_url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        print("请在飞书中扫码确认，创建或选择已有应用。如果扫码失败请手动复制链接到浏览器打开。")
    else:
        print("请复制上方链接到浏览器打开，创建或选择已有应用。")
        print("(安装 qrcode 库可在终端直接显示二维码: pip install qrcode)")

    return full_url


def create_app() -> dict[str, str]:
    """Complete flow: register device → show QR → poll → return credentials."""
    reg = begin_registration()

    show_qr(reg["user_code"])

    result = poll_registration(
        "feishu",
        reg["device_code"],
        reg["interval"],
        reg["expires_in"],
    )

    user_info = result.get("user_info") or {}

    # Lark tenant may not return secret from feishu endpoint; retry with lark
    if not result["client_secret"] and user_info.get("tenant_brand") == "lark":
        print("检测到 Lark 租户，切换端点重试...")
        result = poll_registration(
            "lark",
            reg["device_code"],
            reg["interval"],
            reg["expires_in"],
        )
        user_info = result.get("user_info") or {}

    final = {
        "app_id": result["client_id"],
        "app_secret": result["client_secret"],
        "user_open_id": str(user_info.get("open_id") or ""),
        "tenant_brand": str(user_info.get("tenant_brand") or ""),
    }

    print(f"\n✅ 应用创建成功！")
    print(f"  App ID:     {final['app_id']}")
    print(f"  App Secret: {final['app_secret']}")
    if final["user_open_id"]:
        print(f"  Open ID:    {final['user_open_id']}")

    mcp_config = {
        "mcpServers": {
            "ask-user-via-feishu": {
                "type": "stdio",
                "command": "python",
                "args": ["-m", "ask_user_via_feishu"],
                "timeout": 36000000,
                "env": {
                    "APP_ID": final["app_id"],
                    "APP_SECRET": final["app_secret"],
                    "OWNER_OPEN_ID": final.get("user_open_id") or "ou_xxx",
                },
            }
        }
    }
    print(f"\n📋 MCP 配置（复制到 mcp.json 中）：")
    print(json.dumps(mcp_config, ensure_ascii=False, indent=2))

    return final


def main() -> None:
    print("🤖 飞书应用一键创建")
    print("=" * 40)
    try:
        create_app()
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(1)
    except AppCreationError as exc:
        print(f"\n❌ {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
