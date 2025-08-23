import yt_dlp
import os

def leer_links(archivo="links.txt"):
    if not os.path.exists(archivo):
        print(f"❌ El archivo {archivo} no existe.")
        return []

    with open(archivo, "r", encoding="utf-8") as f:
        enlaces = [line.strip() for line in f if line.strip()]

    if not enlaces:
        print(f"❌ No se encontraron enlaces en {archivo}")
    else:
        print(f"✅ {len(enlaces)} enlaces encontrados.")
    return enlaces

def descargar_videos(enlaces):
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': '%(title)s.%(ext)s',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in enlaces:
            try:
                print(f"\n⬇️ Descargando: {url}")
                ydl.download([url])
            except Exception as e:
                print(f"❌ Error al descargar {url}: {e}")

if __name__ == "__main__":
    print("=== Instagram Video Downloader 1080p ===")

    links = leer_links()

    if links:
        descargar_videos(links)
    else:
        print("No se pudieron procesar los enlaces.")

    input("\nPresiona Enter para salir...")
