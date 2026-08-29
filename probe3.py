# Проба 3: настоящий stealth (v2 API) + 4 способа клика по Turnstile + карта логина
import time
from playwright.sync_api import sync_playwright

stealth_err = None
try:
    from playwright_stealth import Stealth
except Exception as e:
    stealth_err = repr(e)

def cf_page(page):
    try:
        t = page.title().lower()
    except Exception:
        t = ""
    return ("just a moment" in t) or ("attention required" in t)

def dump_login(page):
    print("login url:", page.url, "| title:", page.title())
    try:
        for b in page.locator("button").all()[:30]:
            try:
                t = b.inner_text(timeout=1500).strip()
                if t: print("button:", t[:70])
            except Exception: pass
    except Exception: pass
    try:
        for a in page.locator("a").all()[:80]:
            try:
                href = (a.get_attribute("href") or "").lower()
                if any(w in href for w in ("oauth", "auth", "google", "github", "linkedin", "sso")):
                    print("auth-link:", (a.inner_text(timeout=1500).strip() or "(иконка)")[:40], "->", href[:150])
            except Exception: pass
    except Exception: pass
    try:
        for i in page.locator("input").all()[:10]:
            print("input:", i.get_attribute("type"), i.get_attribute("name"), i.get_attribute("placeholder"))
    except Exception: pass
    page.screenshot(path="/tmp/shot3_login.png", full_page=True)

def main():
    print("stealth import:", "OK" if not stealth_err else stealth_err)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-features=IsolateOrigins,site-per-process"],
        )
        ctx = browser.new_context(viewport={"width": 1366, "height": 900},
                                  locale="en-US", timezone_id="America/Los_Angeles")
        if not stealth_err:
            try:
                Stealth().apply_stealth_sync(ctx)
                print("stealth applied")
            except Exception as e:
                print("stealth apply error:", repr(e))
        page = ctx.new_page()
        page.set_default_timeout(45000)
        page.goto("https://www.producthunt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(12)
        print("home title:", page.title())
        if not cf_page(page):
            print("RESULT: NO-CF-WALL (стены нет вообще)"); page.screenshot(path="/tmp/shot3_home.png"); dump_login(page); browser.close(); return

        # дебаг: все фреймы и iframe'ы
        print("--- frames ---")
        for f in page.frames:
            print("frame url:", (f.url or "")[:120])
        print("--- iframes ---")
        for el in page.locator("iframe").all()[:10]:
            try:
                print("iframe src:", (el.get_attribute("src") or "")[:150], "| box:", el.bounding_box())
            except Exception as e:
                print("iframe err:", repr(e)[:80])

        passed = False
        # способ 1: checkbox внутри cf-фрейма
        fl = page.frame_locator("iframe[src*='challenges.cloudflare.com']").first
        for sel in ("input[type='checkbox']", ".cb-lb", "body"):
            try:
                fl.locator(sel).first.click(timeout=6000)
                print("clicked (frame):", sel)
                break
            except Exception as e:
                print("frame click fail:", sel, repr(e)[:70])
        # способ 2: клик по координатам iframe (классика: левый верхний уголок + смещение)
        try:
            box = page.locator("iframe[src*='challenges.cloudflare.com']").first.bounding_box(timeout=5000)
            if box:
                page.mouse.click(box["x"] + 30, box["y"] + box["height"]/2, delay=80)
                print("clicked (coords):", box)
        except Exception as e:
            print("coords click fail:", repr(e)[:90])
        # способ 3: JS-клик внутри фрейма
        try:
            for f in page.frames:
                if "challenges.cloudflare.com" in (f.url or ""):
                    f.evaluate("() => { const c = document.querySelector('input[type=checkbox], .cb-lb, #challenge-stage'); if (c) c.click(); }")
                    print("js click sent")
                    break
        except Exception as e:
            print("js click fail:", repr(e)[:90])

        for i in range(12):
            time.sleep(5)
            if not cf_page(page):
                passed = True
                print(f"CF-PASSED after ~{(i+1)*5+12}s, url:", page.url)
                break
        page.screenshot(path="/tmp/shot3_home.png")
        if not passed:
            print("RESULT: CF-STILL (стена держится даже со stealth)")
            browser.close(); return
        # прошли стену — идём на логин и мапуем форму
        print("-> login page")
        page.goto("https://www.producthunt.com/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)
        if cf_page(page):
            print("CF на логине, ждём 20с..."); time.sleep(20)
        dump_login(page)
        print("RESULT: CF-PASSED + LOGIN-MAP-DONE")
        browser.close()

main()
