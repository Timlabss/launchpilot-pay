# Проба 4: сразу на /login (без главной) + карта формы + попытка кнопки Google
import time
from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import Stealth
except Exception:
    Stealth = None

def cf_page(page):
    try:
        t = page.title().lower()
    except Exception:
        t = ""
    return ("just a moment" in t) or ("attention required" in t)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-features=IsolateOrigins,site-per-process"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900},
                                  locale="en-US", timezone_id="America/Los_Angeles")
        if Stealth:
            try: Stealth().apply_stealth_sync(ctx)
            except Exception as e: print("stealth err:", repr(e)[:80])
        page = ctx.new_page()
        page.set_default_timeout(45000)
        page.goto("https://www.producthunt.com/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(12)
        print("title:", page.title(), "| url:", page.url)
        if cf_page(page):
            time.sleep(25)
            print("after 37s title:", page.title())
        page.screenshot(path="/tmp/shot4_login.png", full_page=True)
        if cf_page(page):
            print("RESULT: CF-WALL-ON-LOGIN (логин-страница тоже под стеной)")
            browser.close(); return
        # нет стены: мапим форму
        print("--- buttons ---")
        for b in page.locator("button").all()[:30]:
            try:
                t = b.inner_text(timeout=1200).strip()
                if t: print("button:", t[:70])
            except Exception: pass
        print("--- auth links ---")
        for a in page.locator("a").all()[:80]:
            try:
                href = (a.get_attribute("href") or "").lower()
                if any(w in href for w in ("oauth", "auth", "google", "github", "linkedin")):
                    print("auth-link:", (a.inner_text(timeout=1200).strip() or "(иконка)")[:40], "->", href[:150])
            except Exception: pass
        # пробуем кнопку Google
        try:
            g = page.locator("button:has-text('Google'), a:has-text('Google')").first
            g.click(timeout=8000)
            print("clicked Google")
            time.sleep(10)
            print("after google -> url:", page.url[:150], "| title:", page.title()[:60])
            page.screenshot(path="/tmp/shot4_google.png", full_page=True)
            if "accounts.google.com" in page.url:
                body = ""
                try: body = page.inner_text("body")[:400]
                except Exception: pass
                print("google page body:", body.replace("\n", " | ")[:300])
                print("RESULT: GOOGLE-FLOW-REACHED (добрались до страницы Гугла)")
            elif cf_page(page):
                print("RESULT: CF-ON-GOOGLE (стена на переходе к Гуглу)")
            else:
                print("RESULT: UNKNOWN (см. скрин)")
        except Exception as e:
            print("google button fail:", repr(e)[:120])
            print("RESULT: LOGIN-PAGE-MAPPED (кнопку Google не нашли — см. список кнопок)")
        browser.close()

main()
