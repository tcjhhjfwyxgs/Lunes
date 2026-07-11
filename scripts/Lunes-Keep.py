import os
import sys
import time
import logging
from seleniumbase import SB

# ================== 配置日志 ==================
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ================== 辅助函数（保持你原来的）==================
def screenshot_path(name):
    return f"screenshot_{name}.png"

def safe_screenshot(sb, path):
    try:
        sb.save_screenshot(path)
    except:
        pass

def mask_url(url):
    return url[:60] + "..." if len(url) > 60 else url

# ================== 处理初始登录页面（已优化）==================
def handle_initial_page(sb, email):
    """处理登录初始页面 - 增强版"""
    try:
        logger.info("正在打开 Lunes 登录页面...")
        sb.open("https://betadash.lunes.host/login?next=/")
        
        sb.wait_for_ready_state_complete()
        sb.sleep(4)
        
        logger.info(f"当前URL: {sb.get_current_url()}")
        logger.info(f"页面标题: {sb.get_title()}")
        
        safe_screenshot(sb, screenshot_path("01-initial-page"))
        
        # 多选择器适配网站改版
        selectors = [
            'input#email',
            'input[type="email"]',
            'input[name="email"]',
            'input[name*="email" i]',
            'input[placeholder*="email" i]',
            'input[autocomplete="username"]'
        ]
        
        email_field = None
        for selector in selectors:
            try:
                if sb.is_element_visible(selector, timeout=10):
                    email_field = sb.find_element(selector)
                    logger.info(f"✅ 找到邮箱输入框: {selector}")
                    break
            except:
                continue
        
        if not email_field:
            logger.error("❌ 未找到邮箱输入框，页面可能已改版")
            safe_screenshot(sb, screenshot_path("01-initial-failed"))
            with open("page_source_debug.html", "w", encoding="utf-8") as f:
                f.write(sb.get_page_source())
            return "page_changed"
        
        email_field.clear()
        email_field.send_keys(email)
        sb.sleep(1.5)
        return "need_login"
        
    except Exception as e:
        logger.exception(f"handle_initial_page 异常: {e}")
        safe_screenshot(sb, screenshot_path("01-initial-error"))
        return "error"


# ================== 登录表单填写与提交（你提供的代码 + 小优化）==================
def fill_and_submit(sb, email, password):
    try:
        logger.info("正在填写登录表单...")
        
        # 使用更稳健的方式填写邮箱（防止已输入）
        try:
            sb.type('input#email', email)
        except:
            pass
        
        time.sleep(0.5)

        try:
            sb.clear('input#password')
        except:
            pass
        sb.type('input#password', password)
        time.sleep(0.5)

        sp = screenshot_path("03-form-filled")
        safe_screenshot(sb, sp)

        logger.info("处理 Turnstile 验证码...")
        already_done = wait_for_turnstile_success(sb, timeout=5)  # 假设你已有此函数
        if already_done:
            logger.info("Turnstile 已自动完成，直接提交")
        else:
            logger.info("Turnstile 未自动完成，尝试点击...")
            for click_attempt in range(3):
                try:
                    sb.uc_gui_click_captcha()
                    logger.info(f"点击验证码第 {click_attempt + 1} 次")
                except Exception as e:
                    logger.warning(f"点击验证码异常: {e}")
                time.sleep(2)
                if wait_for_turnstile_success(sb, timeout=10):
                    break
            else:
                logger.warning("Turnstile 多次尝试后仍未通过，尝试强制提交...")

        sp = screenshot_path("04-before-submit")
        safe_screenshot(sb, sp)

        logger.info("提交登录...")
        submitted = False
        for selector in ['button.submit-btn', 'button[type="submit"]', 'button.btn-primary']:
            try:
                sb.click(selector)
                submitted = True
                logger.info(f"通过 {selector} 提交")
                break
            except:
                continue

        if not submitted:
            try:
                sb.execute_script('document.querySelector("form").submit()')
                submitted = True
                logger.info("通过 JS 提交")
            except:
                logger.error("所有提交方式都失败")
                return False

        logger.info("等待登录结果...")
        time.sleep(6)

        check_and_exit_on_rate_limit(sb, email)  # 假设你已有此函数

        current_url = sb.get_current_url()
        logger.info(f"登录后URL: {mask_url(current_url)}")

        if "/login" in current_url.lower():
            try:
                error_msg = sb.execute_script('''
                    var flash = document.querySelector('.flash-message, .error-message, .alert, [class*="error"]');
                    return flash ? flash.innerText.trim() : '';
                ''')
                if error_msg:
                    logger.error(f"登录错误提示: {error_msg}")
            except:
                pass
            sp = screenshot_path("05-login-failed")
            safe_screenshot(sb, sp)
            logger.error("登录失败 - 仍在登录页")
            return False

        logger.info("✅ 登录成功！")
        return True

    except Exception as e:
        logger.exception(f"fill_and_submit 异常: {e}")
        safe_screenshot(sb, screenshot_path("05-login-exception"))
        return False


# ================== betadash_login 主流程（已优化）==================
def betadash_login(email, password, proxy=None, max_retries=2):
    result = {"success": False, "message": "未知错误", "screenshot": None}
    
    for attempt in range(max_retries):
        try:
            logger.info(f"第 {attempt+1} 次尝试登录...")
            
            with SB(uc=True, headless=True, timeout=30) as sb:   # uc=True 更稳
                if proxy:
                    # 可在此添加代理逻辑
                    pass
                
                init_status = handle_initial_page(sb, email)
                
                if init_status == "need_login":
                    login_ok = fill_and_submit(sb, email, password)
                    if not login_ok:
                        result["message"] = "登录失败（凭据错误或提交异常）"
                        result["screenshot"] = screenshot_path("05-login-failed")
                        logger.warning(result["message"])
                        break

                nav_ok, server_id, screenshot = navigate_to_server(sb, email)  # 假设你已有此函数

                if nav_ok:
                    result.update(
                        success=True,
                        message=f"服务器: {server_id}",
                        screenshot=screenshot,
                    )
                    logger.info("✅ 保活成功！")
                    return result

                if server_id == "NO_SERVER":
                    result.update(
                        success=True,
                        message="登录成功，但该账号下没有服务器",
                        screenshot=screenshot,
                    )
                    logger.warning("⚠️  账号保活成功（无服务器）")
                    return result
                else:
                    result.update(
                        message=f"导航失败: {server_id}",
                        screenshot=screenshot,
                    )
                    logger.error(f"导航失败，不再重试")
                    return result

        except Exception as e:
            logger.exception(f"执行异常: {e}")
            result["message"] = str(e)

    return result


# ================== 主程序 ==================
def main():
    email, password = parse_single_account()   # 假设你已有此函数
    proxy = os.environ.get("PROXY_SERVER")
    display = setup_display()                  # 假设你已有此函数

    try:
        result = betadash_login(email, password, proxy, max_retries=2)

        notify_telegram(
            email=email,
            ok=result["success"],
            msg=result["message"],
            screenshot_file=result.get("screenshot"),
        )

        if result["success"]:
            logger.info("✅ 保活流程完成")
            sys.exit(0)
        else:
            logger.error("❌ 保活失败")
            sys.exit(1)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
