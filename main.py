import json
import os
import re
from typing import Optional

import requests
import yt_dlp
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# Für den Hausgebrauch reicht "*" (alle Herkünfte erlaubt).
# Wer möchte, kann das später auf die eigene Frontend-Domain einschränken.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Aktuelle Modellnamen stehen unter https://ai.google.dev/gemini-api/docs/models
GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """Du bist ein Assistent, der aus Instagram-/TikTok-Bildunterschriften oder Webseiten-Texten Kochrezepte strukturiert herausliest.
Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt in genau diesem Format, ohne Markdown-Codeblock und ohne zusätzlichen Text:
{"name": "...", "zutaten": ["..."], "zubereitung": ["..."], "tags": ["..."], "hinweis": "..."}

- name: kurzer, ansprechender Rezeptname auf Deutsch
- zutaten: einzelne Zutaten mit Menge, falls im Text vorhanden
- zubereitung: einzelne, klar getrennte Zubereitungsschritte auf Deutsch
- tags: 1 bis 3 kurze Kategorien auf Deutsch (z. B. Pasta, Vegetarisch, Dessert, Schnell, Vegan)
- hinweis: nur befüllen, wenn wichtige Angaben (z. B. Mengen) im Text fehlen, sonst leerer String

Wenn der Text keine verwertbaren Rezeptinformationen enthält, setze zutaten und zubereitung auf leere Listen
und erkläre kurz in "hinweis", woran es liegt."""


class AnalyzeRequest(BaseModel):
    url: str
    caption: Optional[str] = None


@app.get("/")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Plattform-Erkennung
# ---------------------------------------------------------------------------

def erkenne_plattform(url: str) -> str:
    host = re.sub(r"^https?://(www\.)?", "", url, flags=re.I).split("/")[0].lower()
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return "website"


# ---------------------------------------------------------------------------
# Instagram / TikTok / YouTube: Bildunterschrift per yt-dlp lesen
# ---------------------------------------------------------------------------

def hole_beschreibung(url: str) -> str:
    """Liest nur die Metadaten (u.a. Bildunterschrift) aus, ohne das Video herunterzuladen."""
    ydl_opts = {"quiet": True, "skip_download": True, "socket_timeout": 15}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return (info.get("description") or "").strip()


# ---------------------------------------------------------------------------
# Webseiten: zuerst strukturierte Rezeptdaten (schema.org/Recipe) versuchen,
# das ist schneller, kostenlos und zuverlässiger als der KI-Umweg.
# Die meisten Rezept-Blogs betten das für Google-Suchergebnisse ohnehin ein.
# ---------------------------------------------------------------------------

def _als_liste(wert):
    if wert is None:
        return []
    return wert if isinstance(wert, list) else [wert]


def _ist_typ_rezept(obj) -> bool:
    t = obj.get("@type")
    return ("Recipe" in t) if isinstance(t, list) else (t == "Recipe")


def _rezept_kandidaten(data):
    if isinstance(data, list):
        for item in data:
            yield from _rezept_kandidaten(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _rezept_kandidaten(data["@graph"])
        yield data


def _text_aus_anweisung(schritt) -> str:
    if isinstance(schritt, str):
        return schritt.strip()
    if isinstance(schritt, dict):
        if schritt.get("text"):
            return str(schritt["text"]).strip()
        if schritt.get("itemListElement"):
            teile = [_text_aus_anweisung(s) for s in _als_liste(schritt["itemListElement"])]
            return " ".join(t for t in teile if t)
    return ""

def _mappe_schema_rezept(obj) -> Optional[dict]:
    if not isinstance(obj, dict) or not _ist_typ_rezept(obj):
        return None

    name = str(obj.get("name") or "").strip()
    zutaten = [str(z).strip() for z in _als_liste(obj.get("recipeIngredient")) if str(z).strip()]

    zubereitung = []
    for schritt in _als_liste(obj.get("recipeInstructions")):
        if isinstance(schritt, dict) and schritt.get("@type") == "HowToSection":
            for teil in _als_liste(schritt.get("itemListElement")):
                text = _text_aus_anweisung(teil)
                if text:
                    zubereitung.append(text)
        else:
            text = _text_aus_anweisung(schritt)
            if text:
                zubereitung.append(text)

    tags = []
    for feld in (obj.get("recipeCategory"), obj.get("keywords")):
        for wert in _als_liste(feld):
            for teil in str(wert).split(","):
                teil = teil.strip()
                if teil and teil not in tags:
                    tags.append(teil)

    if not name or (not zutaten and not zubereitung):
        return None
    return {"name": name, "zutaten": zutaten, "zubereitung": zubereitung, "tags": tags[:3], "hinweis": ""}


def parse_jsonld_rezept(html: str) -> Optional[dict]:
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I
    ):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        for obj in _rezept_kandidaten(data):
            rezept = _mappe_schema_rezept(obj)
            if rezept:
                return rezept
    return None


def hole_von_webseite(url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Rezeptkasten/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    html = resp.text

    strukturiert = parse_jsonld_rezept(html)
    if strukturiert:
        return strukturiert

    # Fallback: groben Fließtext extrahieren und die KI lesen lassen
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:6000]
    if not text:
        raise RuntimeError("Kein Text auf der Seite gefunden.")
    return frage_ki(url, text)


# ---------------------------------------------------------------------------
# KI-Analyse über Gemini
# ---------------------------------------------------------------------------

def frage_ki(link: str, text: str) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY ist auf dem Server nicht gesetzt.")

    prompt = f"Video-Link oder Webseite: {link}\n\nText:\n{text}"
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    kandidaten = data.get("candidates") or []
    if not kandidaten:
        raise RuntimeError(f"Keine Antwort von Gemini erhalten: {data}")

    rohtext = kandidaten[0]["content"]["parts"][0]["text"].strip()
    bereinigt = re.sub(r"^```(json)?", "", rohtext).strip()
    bereinigt = re.sub(r"```$", "", bereinigt).strip()
    return json.loads(bereinigt)


# ---------------------------------------------------------------------------
# Endpunkt
# ---------------------------------------------------------------------------

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    text = (req.caption or "").strip()
    plattform = erkenne_plattform(req.url)

    if not text and plattform == "website":
        try:
            return hole_von_webseite(req.url)
        except Exception as e:
            return {
                "fehler": "Die Webseite konnte nicht automatisch gelesen werden. Bitte füge den Rezepttext manuell ein.",
                "details": str(e),
            }

    if not text:
        try:
            text = hole_beschreibung(req.url)
        except Exception as e:
            return {
                "fehler": "Der Link konnte nicht automatisch gelesen werden. Bitte füge den Text der Bildunterschrift manuell ein.",
                "details": str(e),
            }

    if not text:
        return {"fehler": "Es wurde kein Text zu diesem Video gefunden. Bitte füge den Text der Bildunterschrift manuell ein."}

    try:
        return frage_ki(req.url, text)
    except Exception as e:
        return {"fehler": "Die KI-Analyse ist fehlgeschlagen.", "details": str(e)}
