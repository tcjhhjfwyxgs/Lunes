#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import re
import platform
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

from seleniumbase import SB
from seleniumbase.common.exceptions import TimeoutException

# ================== 配置 ==================
BETADASH_LOGIN_URL = "https://betadash.lunes.host/login"
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("lunes-keep")


# ================== 辅助函数 ==================
def is_linux() -> bool:
    return platform.system().lower() == "linux"


def mask_url(url: str) -> str:
    return re.sub(r'/servers/\d+', '/servers/***', url)


def mask_email(email: str) -> str:
    if '@' not in email:
        return email[:1] + "***"
    local, domain = email.split('@', 1)
    masked_local = local[:1] + "***" if local else "***"
    if '.' in domain:
        parts = domain.split('.')
        tld = parts[-1]
        first_char = domain[0]
        masked_domain = f"{first_char}***.{tld}" if len(parts) > 1 else f"{first_char}***"
    else:
        masked_domain = domain[:1] + "***"
    return f"{masked_local}@{masked_domain}"


def mask_server_id(server_id: str) -> str:
    if not server_id or server_id == "Unknown":
        return server_id
    if len(server_id) <= 2:
        return server_id + "***"
    return server_id[:2] + "***"


def setup_display():
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            logger.info("虚拟显示已启动")
            return display
        except Exception as e:
            logger.error(f"虚拟显示启动失败: {e}")
            sys.exit(1)
    return None


def screenshot_path(name: str) -> str:
    return str(OUTPUT_DIR / f"{datetime.now().strftime('%H%M%S')}-{name}.png")


def safe_screenshot(sb, path: str):
    try:
        sb.save_screenshot(path)
        logger.info(f"📸 截图 → {Path(path).name}")
    except Exception as e:
        logger.warning(f"截图失败: {e}")


def notify_telegram(email: str, ok: bool, msg: str = "", screenshot_file: str = None):
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        status = "✅ 保活成功" if ok else "❌ 保活失败"
        lines = [status, "", f"账号：{email}"]
        if msg:
            lines.append(f"信息：{msg}")
        lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("Lunes Host Auto Keep Alive")
        text = "\n".join(lines)

        if screenshot_file and Path(screenshot_file).exists():
            with open(screenshot_file, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": text},
                    files={"photo": f},
                    timeout=60
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=30
            )
    except Exception as e:
        logger.warning(f"Telegram 通知失败: {e}")


def check_and_exit_on_rate_limit(sb, email: str) -> None:
    try:
        page_source = sb.get_page_source()
        if "Too Many Requests" in page_source or "20 per 1 hour" in page_source:
            logger.error("⛔ 检测到速率限制")
            sp = screenshot_path("rate-limit")
            safe_screenshot(sb, sp)
            notify_telegram(email=email, ok=False, msg="IP 已被限制（Too Many Requests）", screenshot_file=sp)
            sys.exit(1)
    except:
        pass


def parse_single_account() -> tuple[str, str]:
    raw = os.environ.get("LUNES", "").strip()
    if not raw:
        logger.error("未设置环境变量 LUNES")
        sys.exit(1)

    try:
        parts = raw.split("-----")
        if len(parts) >= 2:
            email = parts[0].strip()
            password = parts[1].strip()
            if email and password:
                logger.info(f"读取到账号: {mask_email(email)}")
                return email, password
    except:
        pass
    logger.error("LUNES 格式错误")
    sys.exit(1)


# ================== Cloudflare & Turnstile ==================
def is_cloudflare_interstitial(sb) -> bool:
    try:
        has_login_form = sb.execute_script('''
            return !!(document.querySelector('input#email') || document.querySelector('input[name="email"]'));
        ''')
        if has_login_form:
            return False
        page_source = sb.get_page_source()
        indicators = ["Just a moment", "Verify you are human", "Checking your browser"]
        return any(ind in page_source for ind in indicators)
    except:
        return False


def bypass_cloudflare_interstitial(sb, email: str, max_attempts: int = 3) -> bool:
    for attempt in range(max_attempts):
        try:
            sb.uc_gui_click_captcha()
            time.sleep(6)
            if not is_cloudflare_interstitial(sb):
                return True
        except:
            pass
        time.sleep(3)
    return False


def wait_for_turnstile_success(sb, timeout: int = 30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            success = sb.execute_script('''
                var resp = document.querySelector('input[name="cf-turnstile-response"]');
                return !!(resp && resp.value && resp.value.length > 20);
            ''')
            if success:
                return True
        except:
            pass
        time.sleep(1)
    return False


# ================== 核心函数 ==================
def clear_browser_state(sb):
    try:
        sb.execute_script('window.localStorage.clear(); window.sessionStorage.clear();')
        sb.delete_all_cookies()
    except:
        pass


def handle_initial_page(sb, email: str) -> Optional[str]:
    clear_browser_state(sb)
    sb.uc_open_with_reconnect(BETADASH_LOGIN_URL, reconnect_time=8)
    time.sleep(4)
    check_and_exit_on_rate_limit(sb, email)
    safe_screenshot(sb, screenshot_path("01-initial"))

    selectors = ['input#email', 'input[type="email"]', 'input[name="email"]', 'input[autocomplete="username"]']
    for selector in selectors:
        try:
            sb.wait_for_element_visible(selector, timeout=12)
            sb.clear(selector)
            sb.type(selector, email)
            logger.info(f"✅ 找到并输入邮箱: {selector}")
            return "need_login"
        except:
            continue
    logger.error("❌ 未找到邮箱输入框")
    safe_screenshot(sb, screenshot_path("02-no-email"))
    return None


def fill_and_submit(sb, email: str, password: str) -> bool:
    logger.info("填写登录信息...")
    time.sleep(0.5)

    sb.type('input#password', password)
    time.sleep(0.5)
    safe_screenshot(sb, screenshot_path("03-form-filled"))

    logger.info("处理 Turnstile...")
    if not wait_for_turnstile_success(sb, 5):
        for i in range(3):
            try:
                sb.uc_gui_click_captcha()
            except:
                pass
            time.sleep(2)
            if wait_for_turnstile_success(sb, 8):
                break

    safe_screenshot(sb, screenshot_path("04-before-submit"))

    logger.info("提交登录...")
    for sel in ['button.submit-btn', 'button[type="submit"]']:
        try:
            sb.click(sel)
            break
        except:
            continue
    else:
        sb.execute_script('document.querySelector("form").submit()')

    time.sleep(6)
    check_and_exit_on_rate_limit(sb, email)

    if "/login" in sb.get_current_url():
        safe_screenshot(sb, screenshot_path("05-login-failed"))
        logger.error("登录失败")
        return False

    logger.info("✅ 登录成功！")
    return True


def _extract_server_id(url: str) -> str:
    match = re.search(r'/servers/(\d+)', url)
    return match.group(1) if match else "Unknown"


def navigate_to_server(sb, email: str) -> tuple[bool, str, Optional[str]]:
    time.sleep(3)
    sp = screenshot_path("06-dashboard")
    safe_screenshot(sb, sp)

    current_url = sb.get_current_url()
    logger.info(f"当前URL: {mask_url(current_url)}")

    if "/servers/" in current_url:
        server_id = _extract_server_id(current_url)
        return True, server_id, sp

    # 查找服务器卡片
    selectors = ['a.server-card', 'a[href*="/servers/"]', '.server-card']
    found = None
    for sel in selectors:
        try:
            sb.wait_for_element_visible(sel, timeout=12)
            found = sel
            break
        except:
            continue

    if not found:
        sp_no = screenshot_path("07-no-server")
        safe_screenshot(sb, sp_no)
        return False, "NO_SERVER", sp_no

    logger.info("点击进入服务器...")
    try:
        sb.click(found)
    except:
        sb.execute_script(f"document.querySelector('{found}').click()")

    time.sleep(5)
    final_url = sb.get_current_url()
    safe_screenshot(sb, screenshot_path("08-server-detail"))

    if "/servers/" in final_url:
        return True, _extract_server_id(final_url), screenshot_path("08-server-detail")
    return False, "NAV_ERROR", screenshot_path("08-server-detail")


# ================== 主流程 ==================
def betadash_login(email: str, password: str, proxy=None, max_retries=1):
    result = {"success": False, "message": "未知错误", "screenshot": None}

    for attempt in range(max_retries):
        try:
            with SB(uc=True, headless=True, timeout=30) as sb:
                init_status = handle_initial_page(sb, email)
                if not init_status:
                    result["message"] = "登录页加载失败"
                    break

                if init_status == "need_login":
                    if not fill_and_submit(sb, email, password):
                        result["message"] = "登录失败"
                        break

                nav_ok, server_id, screenshot = navigate_to_server(sb, email)

                if nav_ok:
                    result.update(success=True, message=f"服务器: {server_id}", screenshot=screenshot)
                    logger.info("✅ 保活成功！")
                    return result
                elif server_id == "NO_SERVER":
                    result.update(success=True, message="无服务器", screenshot=screenshot)
                    logger.warning("⚠️ 登录成功但无服务器")
                    return result
                else:
                    result["message"] = f"导航失败: {server_id}"
                    break
        except Exception as e:
            logger.exception(f"异常: {e}")
            result["message"] = str(e)

    return result


# ================== 主程序 ==================
def main():
    email, password = parse_single_account()
    proxy = os.environ.get("PROXY_SERVER")
    display = setup_display()

    try:
        result = betadash_login(email, password, proxy)

        notify_telegram(
            email=email,
            ok=result["success"],
            msg=result["message"],
            screenshot_file=result.get("screenshot")
        )

        if result["success"]:
            sys.exit(0)
        else:
            sys.exit(1)
    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()