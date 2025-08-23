# ig_contact_finder_fixed.py
# Lee usernames desde users.txt y extrae links/bio/contacto de cada perfil de Instagram.
# Corrige: ignora Threads, resuelve linkshim (l.instagram.com/?u=), abre el modal "… y N más".
#
# Uso:
#   python ig_contact_finder_fixed.py --users-file users.txt --out ig_contacts.csv --jsonl ig_contacts.jsonl
#
# Requisitos:
#   pip install playwright
#   python -m playwright install chromium

import asyncio, csv, re, time, random, json, urllib.parse
from pathlib import Path
from typing import Dict, List, Set
from urllib.parse import urlparse, parse_qs, unquote
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

USERS_FILE   = "users.txt"
USER_DATA    = "./ig_profile"           # sesión persistente
OUT_CSV      = "ig_contacts.csv"
OUT_JSONL    = None                     # e.g. "ig_contacts.jsonl"
DELAY_MS     = 250
OPEN_TIMEOUT = 45000

def rand(a,b): return a + random.random()*(b-a)

EMAIL_RE      = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
URL_JSON_RE   = re.compile(r'"(?:external_url|url)"\s*:\s*"(https?://[^"]+)"')
BIO_RE        = re.compile(r'"biography"\s*:\s*"(.*?)"', re.S)

# Hosts que NO queremos (Threads, ayuda IG, etc.)
EXCLUDE_HOSTS = {
    "threads.net", "help.instagram.com", "about.instagram.com",
    "privacycenter.instagram.com", "transparency.meta.com",
    "meta.ai", "ai.meta.com", "developers.facebook.com",
}
# Dominios IG propios (se permiten solo para linkshim)
INSTAGRAM_HOSTS = {"www.instagram.com", "instagram.com", "l.instagram.com"}

def unshim_instagram(url: str) -> str:
    """Si el link viene desde l.instagram.com, devuelve el destino real (parámetro u=)."""
    try:
        p = urlparse(url)
        if p.hostname and p.hostname.lower() == "l.instagram.com":
            qs = parse_qs(p.query)
            if "u" in qs and qs["u"]:
                return unquote(qs["u"][0])
    except:  # noqa
        pass
    return url

def norm_url(u: str) -> str:
    if not u: return ""
    u = u.strip().replace("\\u0026", "&").replace("\\/", "/")
    u = unshim_instagram(u)
    # normaliza esquema
    if u.startswith("//"):
        u = "https:" + u
    return u

def is_allowed_external(u: str) -> bool:
    try:
        h = (urlparse(u).hostname or "").lower()
    except:
        return False
    if not h:
        return False
    if h in INSTAGRAM_HOSTS:
        # Solo aceptamos si venía con linkshim y ya fue desenmascarado a otro host
        return False
    if h in EXCLUDE_HOSTS:
        return False
    return True

def categorize(links: List[str]) -> Dict[str, List[str]]:
    cats = {"facebook": [], "whatsapp": [], "mailto": [], "tel": [], "web": [], "other": []}
    for u in links:
        lu = u.lower()
        if lu.startswith("mailto:"):
            cats["mailto"].append(u)
        elif lu.startswith("tel:"):
            cats["tel"].append(u)
        elif "facebook.com" in lu or "fb.me" in lu:
            cats["facebook"].append(u)
        elif "wa.me" in lu or "api.whatsapp.com" in lu or "whatsapp.com" in lu:
            cats["whatsapp"].append(u)
        elif lu.startswith("http"):
            cats["web"].append(u)
        else:
            cats["other"].append(u)
    return cats

async def ensure_login(page):
    if "login" in page.url or "/accounts/login" in page.url:
        print("➡️ Inicia sesión en la ventana y presiona ENTER aquí…")
        input()

async def accept_cookies(page):
    try:
        for t in ["Accept", "Aceptar", "Allow", "Permitir", "Allow all cookies", "Aceptar todas"]:
            btn = page.get_by_role("button", name=re.compile(t, re.I))
            if await btn.count():
                await btn.first.click()
                await asyncio.sleep(0.3)
                break
    except:  # noqa
        pass

async def goto_profile(page, user: str):
    url = f"https://www.instagram.com/{user.strip('/')}/"
    await page.goto(url, wait_until="domcontentloaded", timeout=OPEN_TIMEOUT)
    await accept_cookies(page)
    await ensure_login(page)
    try:
        await page.wait_for_selector('header, main', timeout=15000)
    except PWTimeout:
        pass
    return url

# ---------- Modal de “… y N más” ----------
MORE_LINKS_TEXTS = [
    r"\b\d+\s*m[aá]s\b",      # "1 más", "2 más"
    r"\band \d+\s*more\b",    # "and 1 more"
    r"\bmore links\b",        # posibles variantes en inglés
]

async def open_more_links_dialog_if_any(page):
    """Abre el modal de '… y N más' si existe y devuelve el locator del dialog; sino, None."""
    # prueba varias expresiones (ES/EN) tanto en a como en button
    for pattern in MORE_LINKS_TEXTS:
        candidates = [
            page.get_by_role("link", name=re.compile(pattern, re.I)),
            page.get_by_role("button", name=re.compile(pattern, re.I)),
            page.locator(f"a:has-text(/{pattern}/i)"),
            page.locator(f"button:has-text(/{pattern}/i)"),
        ]
        for loc in candidates:
            try:
                if await loc.count():
                    await loc.first.click()
                    dlg = page.locator('div[role="dialog"]').first
                    await dlg.wait_for(state="visible", timeout=6000)
                    await asyncio.sleep(0.2)
                    return dlg
            except:  # noqa
                pass
    return None

async def close_dialog(dlg):
    try:
        btn = dlg.locator('[aria-label="Close"], [aria-label="Cerrar"], [aria-label="Fechar"]').first
        if await btn.count():
            await btn.click()
        else:
            await dlg.page.keyboard.press("Escape")
    except:  # noqa
        pass
    await asyncio.sleep(0.15)

async def collect_links_from_dialog(dlg) -> Set[str]:
    out: Set[str] = set()
    anchors = await dlg.locator('a[href]').all()
    for a in anchors:
        href = await a.get_attribute("href") or ""
        href = norm_url(href)
        if href and is_allowed_external(href):
            out.add(href)
    return out

# ---------- Scrape perfil ----------
async def scrape_profile_contacts(page, user: str) -> Dict:
    profile_url = await goto_profile(page, user)

    # 1) HTML (bio + urls desde JSON embebido)
    html = await page.content()

    # Bio desde JSON embebido
    bio = ""
    m = BIO_RE.search(html)
    if m:
        bio = m.group(1)
        bio = bio.encode("utf-8").decode("unicode_escape").replace("\\n", " ").strip()

    # 2) Links desde DOM visibles debajo de la bio (excluyendo header badges)
    dom_links: Set[str] = set()
    # Anchors fuera del header (para evitar el chip de Threads)
    anchors = await page.locator('main a[href]').all()
    for a in anchors:
        href = await a.get_attribute("href") or ""
        href = norm_url(href)
        if href and is_allowed_external(href):
            dom_links.add(href)

    # 3) Modal “y N más” (si existe)
    more_dlg = await open_more_links_dialog_if_any(page)
    if more_dlg:
        try:
            extra = await collect_links_from_dialog(more_dlg)
            dom_links |= extra
        finally:
            await close_dialog(more_dlg)

    # 4) URLs también desde JSON embebido
    json_links = set(norm_url(u) for u in URL_JSON_RE.findall(html) if u)

    # Merge y filtro final (para evitar meta.ai/threads/etc. aunque aparezcan en JSON)
    all_links = {u for u in (dom_links | json_links) if is_allowed_external(u)}

    # 5) Emails (bio + mailto)
    emails = set(EMAIL_RE.findall(bio or ""))
    for u in all_links:
        if u.lower().startswith("mailto:"):
            emails.add(u.split(":", 1)[1])

    # 6) Categorizar
    cats = categorize(sorted(all_links))

    # 7) Sitio principal: prioriza el primer web que no sea Facebook/WhatsApp
    main_site = cats["web"][0] if cats["web"] else (cats["facebook"][0] if cats["facebook"] else (cats["whatsapp"][0] if cats["whatsapp"] else ""))

    return {
        "user": user,
        "profile_url": profile_url,
        "bio": bio,
        "emails": sorted(emails),
        "links_all": sorted(all_links),
        "website": main_site,
        "facebook_links": cats["facebook"],
        "whatsapp_links": cats["whatsapp"],
        "mailto_links": cats["mailto"],
        "tel_links": cats["tel"],
        "other_links": cats["other"],
        "ts": int(time.time()),
    }

def read_users(path: str) -> List[str]:
    users = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            u = line.strip().lstrip("@")
            if u:
                users.append(u)
    return users

def write_csv_row(w, row: Dict):
    w.writerow([
        row["user"],
        row["profile_url"],
        ";".join(row["emails"]),
        row["website"],
        ";".join(row["facebook_links"]),
        ";".join(row["whatsapp_links"]),
        ";".join(row["mailto_links"]),
        ";".join(row["tel_links"]),
        ";".join(row["links_all"]),
        row["bio"],
        row["ts"],
    ])

async def main(users_file=USERS_FILE, out_csv=OUT_CSV, out_jsonl=OUT_JSONL, delay_ms=DELAY_MS):
    users = read_users(users_file)
    if not users:
        print(f"⚠️ {users_file} vacío o no encontrado.")
        return

    Path(USER_DATA).mkdir(parents=True, exist_ok=True)

    # preparar CSV
    new_file = not Path(out_csv).exists()
    csv_f = open(out_csv, "a", newline="", encoding="utf-8")
    w = csv.writer(csv_f)
    if new_file:
        w.writerow(["username","profile_url","emails","website","facebook_links","whatsapp_links","mailto_links","tel_links","links_all","bio","ts"])

    jsonl_f = open(out_jsonl, "a", encoding="utf-8") if out_jsonl else None

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            USER_DATA, headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = await ctx.new_page()

        for i, user in enumerate(users, 1):
            try:
                print(f"[{i}/{len(users)}] @{user}…")
                data = await scrape_profile_contacts(page, user)
                write_csv_row(w, data)
                csv_f.flush()
                if jsonl_f:
                    jsonl_f.write(json.dumps(data, ensure_ascii=False) + "\n")
                    jsonl_f.flush()
                print(f"  ✓ emails={len(data['emails'])} links={len(data['links_all'])} website={data['website'] or '-'}")
            except Exception as e:
                print(f"  ⚠️ error @{user}: {e}")
            await asyncio.sleep(delay_ms/1000.0)

        await ctx.close()

    csv_f.close()
    if jsonl_f: jsonl_f.close()
    print(f"✅ Terminado. CSV: {out_csv}" + (f" · JSONL: {out_jsonl}" if out_jsonl else ""))

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Instagram contact finder desde users.txt (con modal y linkshim)")
    p.add_argument("--users-file", default=USERS_FILE, help="Archivo con usernames (uno por línea)")
    p.add_argument("--out", dest="out_csv", default=OUT_CSV, help="CSV de salida")
    p.add_argument("--jsonl", dest="out_jsonl", default=OUT_JSONL, help="Ruta JSONL opcional con datos crudos")
    p.add_argument("--delay-ms", type=int, default=DELAY_MS, help="Delay entre perfiles")
    args = p.parse_args()

    asyncio.run(main(args.users_file, args.out_csv, args.out_jsonl, args.delay_ms))
