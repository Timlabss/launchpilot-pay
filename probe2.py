# Проба 2: Cloudflare-стену пробиваем кликом (headless или headful)
import sys, time
from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import stealth_sync
except Exception:
    stealth_sync = None

MODE = sys.argv[1] if len(sys.argv) > 1 else "headless"
TAG = MODE  # для именов
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def cf_check(page):
    try:
        t = page.title()
    except Exception:
        t = ""
    return "just a moment" in t.lower() or "attention required" in t.lower() or "verify you are human" in t.lower()

def main():
    print("MODE:", MODE, "| stealth:", bool(stealth_sync))
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=(MODE == "headless"),
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-features=IsolateOrigins,site-per-process",
                  "--start-maximized"],
        )
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900},
                                  locale="en-US", timezone_id="America/Los_Angeles")
        if stealth_sync:
            stealth_sync(ctx)
        page = ctx.new_page()
        page.set_default_timeout(45000)
        page.goto("https://www.producthunt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(10)
        print("title:", page.title())
        if not cf_check(page):
            print("RESULT: NO-CF-WALL (просто зашли, без проверки)")
            page.screenshot(path=f"/tmp/shot_{TAG}.png")
            browser.close(); return
        # ищем turnstile iframe и пробуем кликнуть
        fr = page.frame_locator("iframe[src*='challenges.cloudflare.com']")
        clicked = False
        for sel in ("input[type='checkbox']", ".cb-lb", "#challenge-stage"):
            try:
                fr.locator(sel).first.click(timeout=8000)
                clicked = True
                print("clicked:", sel)
                break
            except Exception as e:
                print("click fail:", sel, repr(e)[:90])
        if not clicked:
            print("RESULT: NO-CF-IFRAME-CLICK (не нашёлся чекбокс)")
            page.screenshot(path=f"/tmp/shot_{TAG}.png")
            browser.close(); return
        for i in range(12):
            time.sleep(5)
            t = page.title()
            print(f"wait {i*5+5}s title:", t[:60])
            if not ("just a moment" in t.lower() or "attention required" in t.lower()):
                break
        page.screenshot(path=f"/tmp/shot_{TAG}.png")
        if "just a moment" not in page.title().lower():
            print("RESULT: CF-PASSED (стена пройдена!", MODE, ")  url:", page.url)
        else:
            print("RESULT: CF-STILL (стена держится,", MODE, ")")
        browser.close()

main()
