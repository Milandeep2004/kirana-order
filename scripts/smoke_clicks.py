"""Click-through smoke check: logs in as each demo role on a phone-sized screen, visits every screen,
presses every enabled button and link, and reports page errors, failed requests and clicks that did nothing.

Run against a local server:  BASE=http://localhost:8000 python scripts/smoke_clicks.py
(needs `pip install playwright` and `playwright install chromium`)
"""
import os
import sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:8000")
SKIP_TEXT = ("Log out", "Print", "हिंदी", "English")          # leaving the session / opening the print dialog is not a dead button
ROUTES = {
    "retailer": ["#/catalogue", "#/message", "#/orders", "#/cart"],
    "desk": ["#/desk", "#/desk/order/1"],
}
problems = []


def benign(el):
    """Pressing something that is already selected, or minus at zero, is allowed to do nothing."""
    return el.evaluate("""e => e.getAttribute('aria-current') === 'page' || e.getAttribute('aria-pressed') === 'true'
        || e.getAttribute('aria-selected') === 'true'
        || (e.getAttribute('aria-label') === '\u2212' && /(^|\\s)0(\\s|$)/.test(e.parentElement.innerText))""")


def fresh(pg, route):
    """A real reload (a hash-only change would keep the page's in-memory state) so every press starts clean."""
    pg.goto("about:blank")
    pg.goto(BASE + "/" + route)
    pg.wait_for_timeout(1400)


def snapshot(pg):
    return pg.evaluate("document.location.hash + '|' + document.body.innerText + '|' + document.body.innerHTML.length")


def run_role(b, role, acct_index):
    ctx = b.new_context(viewport={"width": 390, "height": 800})
    pg = ctx.new_page()
    reqs = []
    pg.on("pageerror", lambda e: problems.append((role, "page error", str(e))))
    pg.on("console", lambda m: m.type == "error" and "401" not in m.text and problems.append((role, "console error", m.text)))
    pg.on("request", lambda r: reqs.append(r.url))
    pg.on("dialog", lambda d: d.dismiss())  # never really cancel an order
    pg.on("response", lambda r: r.status >= 400 and not (r.status == 401 and "/api/me" in r.url) and problems.append((role, f"HTTP {r.status}", r.url)))
    pg.goto(BASE + "/")
    pg.wait_for_selector(".acct")
    pg.locator(".acct").nth(acct_index).click()
    pg.wait_for_timeout(1200)
    clicked = 0
    for route in ROUTES[role]:
        fresh(pg, route)
        # a first pass puts something in the cart so cart/checkout controls exist
        if route == "#/catalogue":
            plus = pg.locator('button[aria-label="+"]').first
            if plus.count():
                plus.click(); pg.wait_for_timeout(500)
        n = pg.locator("button:visible, a:visible").count()
        print(f"  {role} {route}: {n} controls")
        for i in range(n):
            fresh(pg, route)
            els = pg.locator("button:visible, a:visible")
            if i >= els.count():
                continue  # earlier presses legitimately changed the cart, so fewer controls remain
            el = els.nth(i)
            label = (el.inner_text() or el.get_attribute("aria-label") or "").strip().replace("\n", " ")[:40]
            if any(s in label for s in SKIP_TEXT) or el.is_disabled():
                continue
            before, nreq = snapshot(pg), len(reqs)
            try:
                el.click(timeout=2000)
            except Exception as e:  # not clickable (covered, detached): that is itself a finding
                problems.append((role, f"{route} cannot click '{label}'", str(e).splitlines()[0])); continue
            pg.wait_for_timeout(450)
            clicked += 1
            if snapshot(pg) == before and len(reqs) == nreq and not benign(el):
                problems.append((role, f"{route} DEAD? '{label}'", "nothing changed and no request was sent"))
    print(f"{role}: pressed {clicked} controls")
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    run_role(b, "retailer", 0)
    run_role(b, "desk", 2)
    b.close()
for role, kind, detail in problems:
    print(f"[{role}] {kind}: {detail}")
print("OK: nothing found" if not problems else f"{len(problems)} finding(s)")
sys.exit(1 if problems else 0)
