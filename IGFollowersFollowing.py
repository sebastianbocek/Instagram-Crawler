# ig_user_network_fixed.py
# Captura FOLLOWERS y FOLLOWING de un usuario, genera CSVs y además
# crea:
#   - not_following_back_{user}.csv  -> A quienes sigues y NO te siguen
#   - fans_you_dont_follow_{user}.csv -> Quienes te siguen y vos NO sigues
#
# Uso:
#   python ig_user_network_fixed.py --user "instagram" --max 0 --delay-ms 300
#
# Requisitos:
#   pip install playwright
#   python -m playwright install chromium

import asyncio, csv, re, time, random
from pathlib import Path
from typing import List, Set, Tuple
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ----------------- Config por defecto -----------------
TARGET_USER   = "instagram"     # <-- cambia o usa --user
DELAY_MS      = 350             # pausa entre scrolleos (ms)
MAX_FETCH     = 0               # 0 = sin límite
USER_DATA     = "./ig_profile"  # sesión persistente (cookies)
STABLE_LIMIT  = 16              # iteraciones sin crecer para cortar
OPEN_TIMEOUT  = 45000

FOLLOWERS_BTN_SEL  = 'a[href$="/followers/"]'
FOLLOWING_BTN_SEL  = 'a[href$="/following/"]'
DIALOG_SEL         = 'div[role="dialog"]'

def rand(a,b):
    return a + random.random()*(b-a)

# ----------------- Helpers sesión / navegación -----------------
async def ensure_login(page):
    if "login" in page.url or "/accounts/login" in page.url:
        print("➡️ Inicia sesión en la ventana y presiona ENTER aquí…")
        input()

async def goto_profile(page, user: str):
    url = f"https://www.instagram.com/{user.strip('/')}/"
    await page.goto(url, wait_until="domcontentloaded", timeout=OPEN_TIMEOUT)
    if "login" in page.url:
        await ensure_login(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=OPEN_TIMEOUT)
    try:
        await page.wait_for_selector('header, main', timeout=15000)
    except PWTimeout:
        pass

# ----------------- Apertura del modal -----------------
async def open_list_dialog(page, which: str, user: str):
    """which: 'followers' | 'following'"""
    sel = FOLLOWERS_BTN_SEL if which == "followers" else FOLLOWING_BTN_SEL
    btn = page.locator(sel)
    if not await btn.count():
        # Busca por texto/href alternativo dentro de header
        header_links = page.locator('header a[role="link"], header a[href]')
        n = await header_links.count()
        found = False
        for i in range(n):
            el = header_links.nth(i)
            txt = (await el.text_content() or "").strip().lower()
            href = (await el.get_attribute("href") or "")
            if which == "followers":
                if "followers" in txt or "seguidores" in txt or href.endswith("/followers/"):
                    await el.click(); found = True; break
            else:
                if "following" in txt or "seguidos" in txt or "seguindo" in txt or href.endswith("/following/"):
                    await el.click(); found = True; break
        if not found:
            # Fallback por URL directa del listado
            await page.goto(f"https://www.instagram.com/{user}/{which}/", wait_until="domcontentloaded", timeout=OPEN_TIMEOUT)
    else:
        await btn.first.click()

    dlg = page.locator(DIALOG_SEL).first
    await dlg.wait_for(state="visible", timeout=20000)
    await asyncio.sleep(0.2)
    return dlg

# ----------------- Extracción dentro del modal -----------------
USER_RE = re.compile(r"^/([A-Za-z0-9._]+)/$")

def username_from_href(href: str) -> str|None:
    if not href:
        return None
    href = href.split("?")[0]
    m = USER_RE.match(href)
    if not m:
        return None
    u = m.group(1)
    # evita rutas no-usuario
    if u in ("explore","p","reel","tv","accounts","policies"):
        return None
    return u

async def extract_user_batch_from_dialog(dlg) -> List[str]:
    """
    Extrae usernames desde TODOS los anchors del dialog.
    No depende del layout de filas: funciona con virtualización.
    """
    anchors = await dlg.locator('a[href^="/"]').all()
    out = []
    for a in anchors:
        href = await a.get_attribute("href")
        u = username_from_href(href or "")
        if u:
            out.append(u)
    return out

SCROLL_FN = """
(root) => {
  // Encuentra el contenedor realmente scrolleable dentro del dialog.
  const findScrollable = (r) => {
    const all = r.querySelectorAll('*');
    for (const el of all) {
      const style = getComputedStyle(el);
      if ((style.overflowY === 'auto' || style.overflowY === 'scroll' || style.overflow === 'auto' || style.overflow === 'scroll')) {
        if (el.scrollHeight > el.clientHeight + 8) return el;
      }
    }
    // fallback: usa el propio root si no se encuentra otro
    return r;
  };

  const sc = findScrollable(root);
  const beforeTop = sc.scrollTop;
  // avanza ~una pantalla
  sc.scrollTop = Math.min(sc.scrollTop + Math.max(200, sc.clientHeight * 0.9), sc.scrollHeight);
  return { top: sc.scrollTop, height: sc.scrollHeight, moved: sc.scrollTop !== beforeTop };
}
"""

async def scroll_dialog_once(dlg):
    """
    Ejecuta un avance de scroll dentro del contenedor correcto.
    Devuelve dict con {top, height, moved}.
    """
    try:
        return await dlg.evaluate(SCROLL_FN)
    except:
        # respaldo: tecla PageDown sobre el dialog
        try:
            await dlg.focus()
            await dlg.page.keyboard.press("PageDown")
        except:
            pass
        return {"top": 0, "height": 0, "moved": True}

async def scroll_dialog_to_end(dlg, max_items:int=0, delay_ms:int=DELAY_MS) -> List[str]:
    """
    Scrollea el modal mientras:
      - sigan apareciendo nuevos usernames (crecimiento),
      - y no se alcance max_items (si > 0).
    Se corta si STABLE_LIMIT ciclos seguidos no agregan usuarios.
    """
    seen: Set[str] = set()
    stable = 0
    last_len = -1

    while True:
        # 1) Toma snapshot actual
        batch = await extract_user_batch_from_dialog(dlg)
        for u in batch:
            seen.add(u)

        # 2) Tope
        if max_items and len(seen) >= max_items:
            seen = set(list(seen)[:max_items])
            break

        # 3) ¿hubo progreso?
        cur_len = len(seen)
        if cur_len == last_len:
            stable += 1
        else:
            stable = 0
            last_len = cur_len

        if stable >= STABLE_LIMIT:
            break

        # 4) Avanza el scroll
        await scroll_dialog_once(dlg)
        await asyncio.sleep(delay_ms/1000.0)

    return sorted(seen)

async def close_dialog(page):
    dlg = page.locator(DIALOG_SEL).first
    try:
        close_btn = dlg.locator('[aria-label="Close"], [aria-label="Cerrar"], [aria-label="Fechar"]').first
        if await close_btn.count():
            await close_btn.click()
        else:
            await page.keyboard.press("Escape")
    except:
        pass
    await asyncio.sleep(rand(0.12,0.25))

# ----------------- CSV -----------------
def write_csv(path: Path, rows: List[Tuple[str]]):
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["username","profile_url","ts"])
        ts = int(time.time())
        for (u,) in rows:
            w.writerow([u, f"https://www.instagram.com/{u}/", ts])

def write_graph_csv(path: Path, followers: List[str], following: List[str]):
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["username","type","profile_url","ts"])
        ts = int(time.time())
        for u in followers:
            w.writerow([u, "follower", f"https://www.instagram.com/{u}/", ts])
        for u in following:
            w.writerow([u, "following", f"https://www.instagram.com/{u}/", ts])

def write_simple_list_csv(path: Path, users: List[str]):
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["username","profile_url","ts"])
        ts = int(time.time())
        for u in users:
            w.writerow([u, f"https://www.instagram.com/{u}/", ts])

# ----------------- Flujo principal -----------------
async def scrape_follow_list(page, user: str, which: str, max_items:int, delay_ms:int) -> List[str]:
    dlg = await open_list_dialog(page, which, user)
    try:
        users = await scroll_dialog_to_end(dlg, max_items=max_items, delay_ms=delay_ms)
    finally:
        await close_dialog(page)
    # quita al dueño (a veces aparece al tope)
    users = [u for u in users if u.lower() != user.lower()]
    return users

async def main(user: str, delay_ms:int=DELAY_MS, max_items:int=MAX_FETCH, user_data:str=USER_DATA):
    Path(user_data).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data,
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = await ctx.new_page()

        await goto_profile(page, user)

        print(f"▶️ Perfil: @{user} — capturando FOLLOWERS…")
        followers = await scrape_follow_list(page, user, "followers", max_items, delay_ms)
        print(f"✅ Followers capturados: {len(followers)}")

        print(f"▶️ Perfil: @{user} — capturando FOLLOWING…")
        following = await scrape_follow_list(page, user, "following", max_items, delay_ms)
        print(f"✅ Following capturados: {len(following)}")

        # Set para comparaciones O(1)
        followers_set: Set[str] = set(followers)
        following_set: Set[str] = set(following)

        # 1) A quienes sigues y NO te siguen (not_following_back)
        not_following_back = sorted(list(following_set - followers_set))

        # 2) Quienes te siguen y vos NO sigues (fans_you_dont_follow)
        fans_you_dont_follow = sorted(list(followers_set - following_set))

        print(f"⚠️ No te siguen de vuelta: {len(not_following_back)}")
        print(f"⭐ Te siguen y no los sigues: {len(fans_you_dont_follow)}")

        followers_path = Path(f"followers_{user}.csv")
        following_path = Path(f"following_{user}.csv")
        graph_path     = Path(f"follow_graph_{user}.csv")
        not_back_path  = Path(f"not_following_back_{user}.csv")
        fans_path      = Path(f"fans_you_dont_follow_{user}.csv")

        write_csv(followers_path, [(u,) for u in followers])
        write_csv(following_path, [(u,) for u in following])
        write_graph_csv(graph_path, followers, following)
        write_simple_list_csv(not_back_path, not_following_back)
        write_simple_list_csv(fans_path, fans_you_dont_follow)

        print("💾 Guardados:")
        print(f" - {followers_path}")
        print(f" - {following_path}")
        print(f" - {graph_path}")
        print(f" - {not_back_path}   (Sigues → NO te siguen)")
        print(f" - {fans_path}       (Te siguen → NO los sigues)")

        await ctx.close()

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=False, default=TARGET_USER, help="Usuario objetivo (sin @)")
    p.add_argument("--delay-ms", type=int, default=DELAY_MS, help="Delay entre scrolleos (ms)")
    p.add_argument("--max", type=int, default=MAX_FETCH, help="Tope por lista (0 = sin límite)")
    p.add_argument("--user-data", default=USER_DATA, help="Carpeta sesión persistente")
    args = p.parse_args()

    TARGET_USER = args.user
    DELAY_MS    = args.delay_ms
    MAX_FETCH   = args.max
    USER_DATA   = args.user_data

    asyncio.run(main(TARGET_USER, DELAY_MS, MAX_FETCH, USER_DATA))
