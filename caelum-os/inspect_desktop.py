import subprocess
from playwright.sync_api import sync_playwright

# 读 bridge 的 NOX_TOKEN（不打印）
token = subprocess.run(
    ["ssh", "-o", "ConnectTimeout=10", "root@43.133.211.140",
     "systemctl show bridge -p Environment | tr ' ' '\\n' | sed -n 's/^NOX_TOKEN=//p'"],
    capture_output=True, text=True).stdout.strip()

print("token len:", len(token))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 760})
    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")

    # 注入 token 并刷新进主界面
    page.evaluate("(t) => localStorage.setItem('nox-auth-token', t)", token)
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)

    print("innerWidth:", page.evaluate("window.innerWidth"))
    print("hover:", page.evaluate("window.matchMedia('(hover: hover)').matches"))
    print("pointer fine:", page.evaluate("window.matchMedia('(pointer: fine)').matches"))
    print("has #app:", page.locator("#app").count())
    print("nav count:", page.locator("nav").count())
    print("DesktopRail in html:", "DesktopRail" in page.content())
    print("button[title] count:", page.locator("button[title]").count())
    print("svg count:", page.locator("svg").count())
    # 主内容区是否被 marginLeft 72 推开
    try:
        ml = page.locator("#app > div").nth(2).evaluate("el => getComputedStyle(el).marginLeft")
        print("main marginLeft:", ml)
    except Exception as e:
        print("main marginLeft err:", e)
    page.screenshot(path="D:/claude-code/caelum-os/inspect.png")
    browser.close()
