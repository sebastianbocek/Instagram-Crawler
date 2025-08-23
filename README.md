# 📸 Instagram Crawler  

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)  
[![Playwright](https://img.shields.io/badge/Playwright-Automation-green)](https://playwright.dev/)  
[![yt-dlp](https://img.shields.io/badge/yt--dlp-Video%20Downloader-orange)](https://github.com/yt-dlp/yt-dlp)  

**Instagram Crawler** es una herramienta automatizada que permite **recopilar de manera masiva usuarios de Instagram** y **descargar contenido en bloque** (hashtags, ubicaciones, videos).  
Ideal para scraping, growth hacking y análisis de audiencias.  

---

## ✨ Funcionalidades  

- 🔍 **Instagram Hashtag Crawler** → Extrae usuarios que publican con un hashtag.  
- 🌍 **Instagram Locations Crawler** → Extrae usuarios de una ubicación específica.  
- 🎥 **Instagram Bulk Video Downloader** → Descarga videos masivamente desde `links.txt`.  
- 📝 **Block de Notas para BOT** → Atajo rápido (`Ctrl + L`) para abrir enlaces en el navegador.  

---

## 📦 Requisitos  

Instala las dependencias necesarias:  

```bash     
python -m pip install playwright
python -m playwright install chromium
pip install yt-dlp && winget install -e --id Gyan.FFmpeg
```

🚀 Uso

🔹 Extraer usuarios por hashtag

```bash        
python ig_hashtag_users.py --hashtag n8n --per-cycle 6 --delay-ms 300 --max-users 0
``` 

🔹 Extraer usuarios por ubicación

```bash        
python ig_locations.py --location-url "https://www.instagram.com/explore/locations/212999109/los-angeles-california/" --per-cycle 6 --delay-ms 300 --max-users 10
``` 
🔹 Descargar videos en bloque

```bash        
python ig_downloader.py
``` 

<img width="840" height="353" alt="image" src="https://github.com/user-attachments/assets/13241359-b75c-4414-b147-708e9c5f3dc0" />

<img width="723" height="680" alt="image" src="https://github.com/user-attachments/assets/c508210d-85f0-4d4c-abd8-5535399a279c" />

<img width="452" height="263" alt="image" src="https://github.com/user-attachments/assets/6f5a92c6-4d24-4666-9f97-f3a125d20bd1" />

<img width="616" height="256" alt="image" src="https://github.com/user-attachments/assets/dc275780-d898-46f3-90b9-1ae893c306d5" />

<img width="1101" height="334" alt="image" src="https://github.com/user-attachments/assets/b79da8b0-90ea-41f8-869a-e5575376c1a9" />


