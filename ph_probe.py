# Разведка PH: без входа (CF-стена + карта логин-страницы) или с входом (email+pass)
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

def dump_login_page(page):
    print("login url:", page.url, "| title:", page.title())
    if cf_check(page):
        page.screenshot(path="/tmp/shot_login.png")
        print("RESULT: CF-WALL on login"); return
    try:
        for b in page.locator("button").all()[:30]:
            try:
                t = b.inner_text(timeout=1500).strip()
                if t: print("button:", t[:70])
            except Exception: pass
    except Exception: pass
    try:
        for a in page.locator("a").all()[:60]:
            try:
                href = a.get_attribute("href") or ""
                if any(w in href.lower() for w in ("oauth", "auth", "google", "github", "linkedin")):
                    print("auth-link:", (a.inner_text(timeout=1500).strip() or "(иконка)")[:40], "->", href[:140])
            except Exception: pass
    except Exception: pass
    try:
        for i in page.locator("input").all()[:10]:
            print("input:", i.get_attribute("type"), i.get_attribute("name"), i.get_attribute("placeholder"))
    except Exception: pass
    page.screenshot(path="/tmp/shot_login.png")

def main():
    print("email set:", bool(EMAIL), "| pass set:", bool(PASS), "| stealth:", bool(stealth_sync))
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
        print("home url:", page.url, "| title:", page.title())
        page.screenshot(path="/tmp/shot_home.png")
        if cf_check(page):
            print("RESULT: CF-WALL on home (не прошли Cloudflare)"); browser.close(); return
        print("home loaded OK")

        if not (EMAIL and PASS):
            print("-> NO-LOGIN MODE: карта логин-страницы")
            page.goto("https://www.producthunt.com/login", wait_until="domcontentloaded", timeout=60000)
            time.sleep(6)
            dump_login_page(page)
            print("RESULT: NO-LOGIN-SCAN-DONE")
            browser.close()
            return

        page.goto("https://www.producthunt.com/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        dump_login_page(page)
        email_sel = page.locator("input[type='email'], input[name='email'], input[autocomplete='email']").first
        pass_sel = page.locator("input[type='password']").first
        try:
            email_sel.wait_for(timeout=10000)
            pass_sel.wait_for(timeout=10000)
        except Exception as e:
            print("no email/pass form (OAuth-only?)"); print("RESULT: NO-FORM"); browser.close(); return
        email_sel.fill(EMAIL); time.sleep(0.8)
        pass_sel.fill(PASS); time.sleep(0.8)
        try:
            page.locator("button[type='submit']").first.click()
        except Exception:
            pass_sel.press("Enter")
        time.sleep(10)
        url = page.url; title = page.title()
        try: body = page.inner_text("body")[:600]
        except Exception: body = ""
        print("after submit url:", url, "| title:", title)
        print("body:", body.replace("\n", " | ")[:400])
        page.screenshot(path="/tmp/shot_after.png")
        low = (title + " " + body).lower()
        if "login" in url:
            if any(w in low for w in ("incorrect", "invalid", "wrong", "not found", "check your")):
                print("RESULT: BAD-CREDENTIALS")
            elif any(w in low for w in ("verify", "2fa", "code", "authenticat")):
                print("RESULT: 2FA-CODE-NEEDED")
            else:
                print("RESULT: LOGIN-FORM-STILL")
        else:
            print("RESULT: LOGIN-OK")
        browser.close()

main()
