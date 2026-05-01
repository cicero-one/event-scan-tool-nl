# Event Scan Tool NL

Persoonlijke Flask MVP voor EDM, techno, psytrance en queer/LGBTQ+ branded events in Nederland.

## Features
- Flask dashboard met SQLite database
- Dagelijkse scan om 10:00 Europe/Amsterdam via APScheduler
- Instelbare genrevoorkeuren
- Match score met alert vanaf 80/100
- Club max €100, festival max €800 standaard
- Email-alerts naar `Tom.loijer@gmail.com`
- Ticketmaster Discovery API connector
- Demo-events als fallback wanneer er nog geen Ticketmaster API key is

## Belangrijke beperking
TicketSwap heeft geen publieke officiële API voor algemene event/prijsdata. Deze MVP bewaart het connectorpunt, maar gebruikt nog geen ongeautoriseerde scraping. Voor productie kun je TicketSwap-links handmatig toevoegen, een toegestane partner/API route gebruiken, of prijsdata via eigen opgeslagen links ophalen waar toegestaan.

## Installatie
```bash
cd event_scan_tool_nl
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open daarna: http://localhost:5000

## Email instellen
Voor Gmail gebruik je bij voorkeur een Google App Password.
Zet in `.env`:
```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=your_email@gmail.com
ALERT_EMAIL_TO=Tom.loijer@gmail.com
```

## Ticketmaster instellen
Maak een Ticketmaster developer key aan en zet:
```env
TICKETMASTER_API_KEY=...
```

De app zoekt met `countryCode=NL` en de genres/zoektermen uit je instellingen.

## Productie-notities
- Gebruik voor productie liever Postgres i.p.v. SQLite.
- Draai de app via gunicorn/uwsgi en gebruik een aparte worker voor scheduled scans.
- Voeg officiële eventwebsites toe als eigen connectors in `fetch_*` functies.
- Voeg deduplicatie toe op naam+datum+venue wanneer meerdere bronnen hetzelfde event tonen.
