# Rezeptkasten-Backend

Kleines Backend, das aus einem Instagram-Reel- oder TikTok-Link automatisch ein Rezept herausliest.

## Was es macht

1. Nimmt einen Link entgegen (und optional bereits eingefügten Text).
2. Bei Instagram/TikTok/YouTube: liest mit `yt-dlp` nur die Metadaten aus (Bildunterschrift/Beschreibung) — es wird **kein Video heruntergeladen**.
3. Bei jedem anderen Link (Rezept-Blogs etc.): lädt die Seite und sucht zuerst nach eingebetteten, strukturierten Rezeptdaten (schema.org/Recipe, JSON-LD) — das nutzen die meisten Rezept-Webseiten für Google und liefert exakte Ergebnisse ganz ohne KI. Nur falls das fehlt, wird der Seitentext an die KI geschickt.
4. Schickt Text ohne strukturierte Daten an die kostenlose Gemini-API von Google, die daraus Name, Zutaten, Zubereitung und Tags herausliest.
5. Gibt das Ergebnis als JSON zurück.

## Lokal testen

```
pip install -r requirements.txt
export GEMINI_API_KEY=dein_key
uvicorn main:app --reload
```

Dann im Browser `http://127.0.0.1:8000/` öffnen — dort sollte `{"status":"ok"}` erscheinen.

## Kostenlos deployen (Render.com)

1. Diesen Ordner (main.py, requirements.txt) in ein neues GitHub-Repository laden.
2. Auf render.com registrieren → "New" → "Web Service" → das Repo auswählen.
3. Als Start Command eintragen: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Unter "Environment" die Variable `GEMINI_API_KEY` mit deinem Key aus Google AI Studio (aistudio.google.com/apikey) eintragen.
5. Deploy klicken. Die URL, die Render dir gibt (z. B. `https://dein-name.onrender.com`), trägst du im Frontend unter „Einstellungen" (⚙) ein.

## Wichtig zu wissen

- Der kostenlose Render-Tarif "schläft" nach ein paar Minuten Inaktivität ein — der erste Aufruf danach kann 30–60 Sekunden dauern.
- Instagram und TikTok ändern gelegentlich ihre Seiten, wodurch `yt-dlp` kurzzeitig kaputtgehen kann. Abhilfe: Im Repo `pip install -U yt-dlp` laufen lassen bzw. einfach neu deployen, sobald ein neues yt-dlp-Release erschienen ist.
- Funktioniert am besten, wenn im Reel/TikTok eine Bildunterschrift mit Zutaten/Zubereitung steht (bei den meisten Koch-Videos der Fall). Steht das Rezept nur gesprochen oder eingeblendet im Video, schlägt die automatische Erkennung fehl — dann im Frontend den Text einfach von Hand ins Feld "Text der Bildunterschrift" einfügen oder die Felder direkt selbst ausfüllen.
- Die Gemini-Free-Tier-Limits reichen für den privaten Gebrauch gut aus, sind aber nicht für viele gleichzeitige Nutzer gedacht.
