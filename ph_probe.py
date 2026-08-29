# Разовая разведка: может ли stealth-браузер на GitHub-раннере войти в PH
import os, time
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
except Exception:
    stealth_sync = None

EMAIL = os.environ.get("PH_EMAIL", "").strip()
PASS = os.environ.get("PH_PASS", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def cf_check(page):
    try:
        t = page.title()
    except Exception:
        t = ""
    return "just a moment" in t.lower() or "attention required" in t.lower()

def main():
    print("email set:", bool(EMAIL), "| pass set:", bool(PASS), "| stealth:", bool(stealth_sync))
    if not (EMAIL and PASS):
        print("NO CREDENTIALS"); return
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-features=IsolateOrigins,site-per-process"],
        )
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900},
                                  locale="en-US", timezone_id="America/Los_Angeles")
        if stealth_sync:
            stealth_sync(ctx)
        page = ctx.new_page()
        page.set_default_timeout(45000)

        print("-> home page")
        try:
            page.goto("https://www.producthunt.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("goto error:", repr(e))
        time.sleep(8)
        print("home url:", page.url)
        print("home title:", page.title())
        page.screenshot(path="/tmp/shot_home.png")
        if cf_check(page):
            print("RESULT: CF-WALL on home (не прошли Cloudflare)"); browser.close(); return

        print("-> login page")
        page.goto("https://www.producthunt.com/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        print("login url:", page.url, "| title:", page.title())
        if cf_check(page):
            page.screenshot(path="/tmp/shot_login.png")
            print("RESULT: CF-WALL on login"); browser.close(); return

        email_sel = page.locator("input[type='email'], input[name='email'], input[autocomplete='email']").first
        pass_sel = page.locator("input[type='password']").first
        try:
            email_sel.wait_for(timeout=20000)
            pass_sel.wait_for(timeout=20000)
        except Exception as e:
            print("no standard form:", repr(e))
            page.screenshot(path="/tmp/shot_login.png")
            try:
                print(page.inner_text("body")[:800].replace("\n", " | "))
            except Exception:
                pass
            print("RESULT: NO-FORM (видимо не то, что ждали)"); browser.close(); return

        email_sel.fill(EMAIL); time.sleep(0.8)
        pass_sel.fill(PASS); time.sleep(0.8)
        print("-> submit")
        try:
            page.locator("button[type='submit']").first.click()
        except Exception as e:
            print("submit click error:", repr(e))
            pass_sel.press("Enter")
        time.sleep(10)
        url = page.url; title = page.title()
        body = ""
        try:
            body = page.inner_text("body")[:600]
        except Exception:
            pass
        print("after submit url:", url, "| title:", title)
        print("body:", body.replace("\n", " | ")[:400])
        page.screenshot(path="/tmp/shot_after.png")
        low = (title + " " + body).lower()
        if "login" in url or "signin" in url:
            if any(w in low for w in ("incorrect", "invalid", "wrong", "doesn't match", "not found", "check your")):
                print("RESULT: BAD-CREDENTIALS")
            elif any(w in low for w in ("verify", "2fa", "two-factor", "code", "authenticat")):
                print("RESULT: 2FA-CODE-NEEDED")
            elif "just a moment" in low:
                print("RESULT: CF-WALL after submit")
            else:
                print("RESULT: LOGIN-FORM-STILL (не ушло)")
        else:
            print("RESULT: LOGIN-OK (покинули страницу логина, url выше)")
        browser.close()

main()
