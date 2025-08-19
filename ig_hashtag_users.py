import asyncio, csv, os, re, time, random
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

HASHTAG      = "n8n"      # <-- cambia aquí o usa --hashtag
PER_CYCLE    = 6          # abrir 6 posts por ciclo (tu flujo)
DELAY_MS     = 300        # pausa entre posts
MAX_USERS    = 300        # corta al llegar a N usuarios (0 = sin límite)
OUT_CSV      = "ig_users.csv"
USER_DATA    = "./ig_profile"  # carpeta para persistir sesión (cookies)

POST_SEL = 'a[href^="/p/"], a[href^="/reel/"], a[href^="/tv/"]'
DIALOG_SEL = 'div[role="dialog"]'

def rand(a,b): return a + random.random()*(b-a)

async def ensure_login(page):
    # Si no estás logueado, esperá a que aparezca el login y dejá que el user lo haga manualmente.
    if "login" in page.url:
        print("➡️ Inicia sesión y presiona ENTER aquí en consola…")
        input()

async def get_visible_tiles(page):
    tiles = await page.locator(POST_SEL).element_handles()
    vp = await page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
    visibles = []
    for h in tiles:
        box = await h.bounding_box()
        if not box: continue
        if 100 <= box["y"] <= vp["h"] - 80:
            visibles.append(h)
    return visibles

async def wait_for_grid(page, timeout=10000):
    await page.wait_for_selector(POST_SEL, timeout=timeout)

async def extract_username_from_dialog(dlg):
    # 1) link en header: /<user>/
    try:
        a = dlg.locator('header a[href^="/"]:not([href*="/p/"]):not([href*="/reel/"]):not([href*="/tv/"])').first
        href = await a.get_attribute("href")
        if href:
            m = re.match(r"^/([A-Za-z0-9._]+)/$", href)
            if m: return m.group(1)
        txt = (await a.text_content() or "").strip()
        if re.fullmatch(r"[A-Za-z0-9._]{2,}", txt):
            return txt
    except PWTimeout:
        pass
    # 2) anchors genéricos con texto de usuario
    try:
        anchors = await dlg.locator('a[role="link"]').all()
        for an in anchors:
            txt = (await an.text_content() or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._]{2,}", txt):
                return txt
    except:
        pass
    # 3) regex del HTML del modal
    html = await dlg.inner_html()
    m = re.search(r'"username"\s*:\s*"([A-Za-z0-9._]+)"', html)
    if m: return m.group(1)
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)/", html)
    if m: return m.group(1)
    return None

async def click_and_grab_username(page, visited_posts:set):
    # toma el primer post visible NO visitado
    tiles = await get_visible_tiles(page)
    for t in tiles:
        href = await t.get_attribute("href")
        if not href: continue
        url_norm = page.url.split("/explore")[0] + href.split("?")[0]
        if url_norm in visited_posts: 
            continue
        visited_posts.add(url_norm)

        await t.scroll_into_view_if_needed()
        await asyncio.sleep(rand(0.05,0.15))
        await t.click()
        try:
            dlg = page.locator(DIALOG_SEL)
            await dlg.wait_for(state="visible", timeout=6000)
            user = await extract_username_from_dialog(dlg)
        except PWTimeout:
            user = None
        finally:
            # cerrar modal
            try:
                # botón close
                close_btn = dlg.locator('[aria-label="Close"], [aria-label="Cerrar"]').first
                if await close_btn.count():
                    await close_btn.click()
                else:
                    await page.keyboard.press("Escape")
            except:
                pass
        await asyncio.sleep(DELAY_MS/1000.0)
        return user
    return None

async def main(hashtag=HASHTAG, per_cycle=PER_CYCLE, delay_ms=DELAY_MS, max_users=MAX_USERS):
    Path(USER_DATA).mkdir(parents=True, exist_ok=True)
    users, visited = set(), set()

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            USER_DATA, headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/118.0 Safari/537.36"
        )
        page = await ctx.new_page()
        url = f"https://www.instagram.com/explore/tags/{hashtag.strip('#')}/"
        await page.goto(url, wait_until="domcontentloaded")
        await ensure_login(page)
        await page.goto(url)
        await wait_for_grid(page)

        # preparar CSV
        new_file = not Path(OUT_CSV).exists()
        csv_f = open(OUT_CSV, "a", newline="", encoding="utf-8")
        writer = csv.writer(csv_f)
        if new_file:
            writer.writerow(["username","profile_url","ts"])

        print(f"▶️ Empezando en #{hashtag}…")
        try:
            idle_cycles = 0
            while True:
                # ciclo: abrir/leer/cerrar 6 posts
                got_in_cycle = 0
                for _ in range(per_cycle):
                    user = await click_and_grab_username(page, visited)
                    if user:
                        if user not in users:
                            users.add(user)
                            writer.writerow([user, f"https://www.instagram.com/{user}/", int(time.time())])
                            csv_f.flush()
                            print(f"+ @{user}  (total: {len(users)})")
                        got_in_cycle += 1
                    else:
                        # no encontró visible → corta para scrollear
                        break
                # scroll y pausa
                await page.mouse.wheel(0, 900)
                await asyncio.sleep(rand(0.6, 1.2))

                if got_in_cycle == 0:
                    idle_cycles += 1
                else:
                    idle_cycles = 0

                if idle_cycles >= 6:
                    print("ℹ️ No aparecen más posts nuevos en el grid. Fin.")
                    break

                if max_users and len(users) >= max_users:
                    print("✅ Tope de usuarios alcanzado.")
                    break
        finally:
            csv_f.close()
            await ctx.close()

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--hashtag", default=HASHTAG)
    p.add_argument("--per-cycle", type=int, default=PER_CYCLE)
    p.add_argument("--delay-ms", type=int, default=DELAY_MS)
    p.add_argument("--max-users", type=int, default=MAX_USERS)
    a = p.parse_args()
    # set globals from args for simplicity
    HASHTAG, PER_CYCLE, DELAY_MS, MAX_USERS = a.hashtag, a.per_cycle, a.delay_ms, a.max_users
    asyncio.run(main(HASHTAG, PER_CYCLE, DELAY_MS, MAX_USERS))
