# igl_location_tabs.py
# Ubicaciones de Instagram → abre cada publicación en NUEVA PESTAÑA y extrae @.
# Uso:
#   python igl_location_tabs.py --location-url "https://www.instagram.com/explore/locations/212999109/los-angeles-california/" --per-cycle 6 --delay-ms 300 --max-users 300
#
# Requisitos:
#   pip install playwright
#   python -m playwright install chromium

import asyncio, csv, re, time, random
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

LOCATION_URL = "https://www.instagram.com/explore/locations/212999109/los-angeles-california/"
PER_CYCLE    = 6
DELAY_MS     = 300
MAX_USERS    = 300
OUT_CSV      = "ig_users.csv"
USER_DATA    = "./ig_profile"

POST_SEL   = 'a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]'

def rand(a,b): return a + random.random()*(b-a)

async def ensure_login(page):
    if "login" in page.url:
        print("➡️ Inicia sesión en la ventana y presiona ENTER aquí en consola…")
        input()

async def accept_cookies(page):
    try:
        candidates = [
            "Allow all cookies","Allow essential cookies","Accept all",
            "Aceptar todas las cookies","Permitir todas","Aceptar",
            "Solo esenciales","Sólo esenciales","Permitir"
        ]
        for t in candidates:
            btn = page.get_by_role("button", name=re.compile(t, re.I))
            if await btn.count():
                await btn.first.click()
                await asyncio.sleep(0.3)
                break
    except: pass

async def wait_for_any_post(page, timeout_ms=20000):
    """Espera hasta que haya al menos 1 enlace de post en el DOM."""
    start = time.time()
    while (time.time() - start)*1000 < timeout_ms:
        try:
            cnt = await page.locator(POST_SEL).count()
            if cnt > 0:
                return
        except: pass
        # fuerza algo de scroll para cargar grid
        await page.mouse.wheel(0, 900)
        await asyncio.sleep(0.5)
    raise PWTimeout(f"No se encontró ningún post ({POST_SEL}) en {timeout_ms}ms")

async def collect_grid_links(page, limit=60):
    """Devuelve hasta 'limit' hrefs de posts presentes en el grid (sin duplicados)."""
    hrefs = set()
    handles = await page.locator(POST_SEL).element_handles()
    for h in handles:
        href = await h.get_attribute("href")
        if not href: continue
        # normaliza a absoluto
        absu = urljoin("https://www.instagram.com/", href.split("?")[0])
        hrefs.add(absu)
        if len(hrefs) >= limit:
            break
    return list(hrefs)

async def extract_username_from_post_page(post_page):
    """Extrae @ desde la PÁGINA del post (más robusto que modal)."""
    # 1) Header -> anchor perfil
    try:
        a = post_page.locator('header a[href^="/"]:not([href*="/p/"]):not([href*="/reel/"]):not([href*="/tv/"])').first
        if await a.count():
            href = await a.get_attribute("href")
            if href:
                m = re.match(r"^/([A-Za-z0-9._]+)/?$", href)
                if m: return m.group(1)
            txt = (await a.text_content() or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._]{2,}", txt):
                return txt
    except: pass

    # 2) Cualquier link con pinta de usuario
    try:
        anchors = await post_page.locator('a[role="link"]').all()
        for an in anchors:
            txt = (await an.text_content() or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._]{2,}", txt):
                return txt
    except: pass

    # 3) HTML (busca "username": "...") - suele estar en JSON embebido
    try:
        html = await post_page.content()
        m = re.search(r'"username"\s*:\s*"([A-Za-z0-9._]+)"', html)
        if m: return m.group(1)
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)/", html)
        if m: return m.group(1)
    except: pass

    return None

async def open_and_grab(page_context, post_url, delay_ms):
    """Abre el post en nueva pestaña, extrae username y cierra."""
    p = await page_context.new_page()
    try:
        await p.goto(post_url, wait_until="domcontentloaded", timeout=45000)
        if "login" in p.url:
            print("ℹ️ Login wall en post. Inicia sesión una vez en la sesión persistente y reintenta.")
            return None
        # espera a que aparezca algo del header o contenido
        try:
            await p.wait_for_selector('header, article', timeout=8000)
        except PWTimeout:
            pass
        user = await extract_username_from_post_page(p)
        await asyncio.sleep(delay_ms/1000.0)
        return user
    finally:
        await p.close()

async def main(location_url=LOCATION_URL, per_cycle=PER_CYCLE, delay_ms=DELAY_MS, max_users=MAX_USERS):
    Path(USER_DATA).mkdir(parents=True, exist_ok=True)
    users, visited = set(), set()

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            USER_DATA, headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
        page = await ctx.new_page()
        await page.goto(location_url, wait_until="domcontentloaded")
        await accept_cookies(page)
        await ensure_login(page)

        # Asegura que exista algún post en el DOM
        await wait_for_any_post(page, timeout_ms=25000)

        new_file = not Path(OUT_CSV).exists()
        csv_f = open(OUT_CSV, "a", newline="", encoding="utf-8")
        writer = csv.writer(csv_f)
        if new_file:
            writer.writerow(["username","profile_url","ts"])

        print(f"▶️ Empezando en ubicación: {location_url}")
        try:
            idle_cycles = 0
            while True:
                # recolecta una tanda de links del grid
                links = await collect_grid_links(page, limit=per_cycle*2)
                got_in_cycle = 0

                for href in links:
                    if href in visited: 
                        continue
                    visited.add(href)

                    user = await open_and_grab(ctx, href, delay_ms)
                    if user:
                        if user not in users:
                            users.add(user)
                            writer.writerow([user, f"https://www.instagram.com/{user}/", int(time.time())])
                            csv_f.flush()
                            print(f"+ @{user}  (total: {len(users)})")
                        got_in_cycle += 1
                        if max_users and len(users) >= max_users:
                            print("✅ Tope de usuarios alcanzado.")
                            return
                    # corta cuando cumplimos la cuota del ciclo
                    if got_in_cycle >= per_cycle:
                        break

                # si no agarramos nada en el ciclo, scrollea más fuerte
                if got_in_cycle == 0:
                    idle_cycles += 1
                    await page.mouse.wheel(0, 1600)
                    await asyncio.sleep(rand(0.6, 1.2))
                else:
                    idle_cycles = 0
                    # scroll normal entre ciclos
                    await page.mouse.wheel(0, 900)
                    await asyncio.sleep(rand(0.6, 1.0))

                if idle_cycles >= 6:
                    print("ℹ️ No aparecen más posts nuevos en el grid. Fin.")
                    break

        finally:
            csv_f.close()
            await ctx.close()

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--location-url", default=LOCATION_URL)
    p.add_argument("--per-cycle", type=int, default=PER_CYCLE)
    p.add_argument("--delay-ms", type=int, default=DELAY_MS)
    p.add_argument("--max-users", type=int, default=MAX_USERS)
    a = p.parse_args()
    LOCATION_URL, PER_CYCLE, DELAY_MS, MAX_USERS = a.location_url, a.per_cycle, a.delay_ms, a.max_users
    asyncio.run(main(LOCATION_URL, PER_CYCLE, DELAY_MS, MAX_USERS))
