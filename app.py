import os
import re
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///events.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

DEFAULT_GENRES = "techno, edm, psytrance, queer, lgbtq, trance, hard techno, melodic techno, house"
LGBTQ_TERMS = ["queer", "lgbt", "lgbtq", "gay", "pride", "drag", "ballroom", "voguing"]
CLUB_TERMS = ["club", "night", "nacht", "rave", "party", "warehouse"]
FESTIVAL_TERMS = ["festival", "weekender", "open air", "outdoor"]

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alert_email = db.Column(db.String(255), default="Tom.loijer@gmail.com")
    genres = db.Column(db.Text, default=DEFAULT_GENRES)
    min_match_score = db.Column(db.Integer, default=80)
    max_club_price = db.Column(db.Float, default=100.0)
    max_festival_price = db.Column(db.Float, default=800.0)
    lgbtq_bonus = db.Column(db.Integer, default=10)
    weekends_only = db.Column(db.Boolean, default=False)
    cities = db.Column(db.Text, default="")

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(80), nullable=False)
    external_id = db.Column(db.String(160), unique=True, nullable=False)
    name = db.Column(db.String(300), nullable=False)
    start_date = db.Column(db.String(50), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    venue = db.Column(db.String(200), nullable=True)
    url = db.Column(db.Text, nullable=True)
    genre_tags = db.Column(db.Text, default="")
    lgbtq_branded = db.Column(db.Boolean, default=False)
    event_type = db.Column(db.String(40), default="club")
    current_price = db.Column(db.Float, nullable=True)
    previous_price = db.Column(db.Float, nullable=True)
    lowest_seen_price = db.Column(db.Float, nullable=True)
    match_score = db.Column(db.Integer, default=0)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    alert_type = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent = db.Column(db.Boolean, default=False)


def get_settings() -> Setting:
    settings = Setting.query.first()
    if not settings:
        settings = Setting(alert_email=os.getenv("ALERT_EMAIL_TO", "Tom.loijer@gmail.com"))
        db.session.add(settings)
        db.session.commit()
    return settings


def norm_list(text: str) -> List[str]:
    return [x.strip().lower() for x in re.split(r"[,;\n]", text or "") if x.strip()]


def infer_type(name: str) -> str:
    text = name.lower()
    if any(t in text for t in FESTIVAL_TERMS):
        return "festival"
    return "club"


def score_event(item: Dict, settings: Setting) -> int:
    score = 0
    genres = norm_list(settings.genres)
    haystack = " ".join(str(item.get(k, "")) for k in ["name", "venue", "city", "genre_tags", "description"]).lower()
    matched_genres = [g for g in genres if g in haystack]
    if matched_genres:
        score += min(35, 20 + 5 * len(matched_genres))
    price = item.get("price")
    event_type = item.get("event_type") or infer_type(item.get("name", ""))
    max_price = settings.max_festival_price if event_type == "festival" else settings.max_club_price
    if price is None:
        score += 10
    elif price <= max_price:
        score += 25
        if price <= max_price * 0.5:
            score += 10
    if item.get("lgbtq_branded"):
        score += settings.lgbtq_bonus
    if item.get("venue"):
        score += 10
    if item.get("start_date"):
        score += 10
    if item.get("url"):
        score += 10
    return min(100, score)


def parse_ticketmaster_event(raw: Dict) -> Dict:
    embedded = raw.get("_embedded", {})
    venues = embedded.get("venues", [{}])
    venue = venues[0] if venues else {}
    city = (venue.get("city") or {}).get("name")
    venue_name = venue.get("name")
    name = raw.get("name", "Untitled event")
    price = None
    price_ranges = raw.get("priceRanges") or []
    if price_ranges:
        price = price_ranges[0].get("min") or price_ranges[0].get("max")
    classifications = raw.get("classifications") or []
    tags = []
    for c in classifications:
        for key in ["segment", "genre", "subGenre", "type", "subType"]:
            val = c.get(key, {}).get("name") if isinstance(c.get(key), dict) else None
            if val:
                tags.append(val.lower())
    text = f"{name} {' '.join(tags)}".lower()
    return {
        "source": "Ticketmaster",
        "external_id": f"ticketmaster:{raw.get('id')}",
        "name": name,
        "start_date": (raw.get("dates", {}).get("start", {}) or {}).get("localDate"),
        "city": city,
        "venue": venue_name,
        "url": raw.get("url"),
        "genre_tags": ", ".join(sorted(set(tags))),
        "lgbtq_branded": any(term in text for term in LGBTQ_TERMS),
        "event_type": infer_type(name),
        "price": float(price) if price is not None else None,
    }


def fetch_ticketmaster_events(settings: Setting) -> List[Dict]:
    api_key = os.getenv("TICKETMASTER_API_KEY")
    if not api_key:
        return []
    results: List[Dict] = []
    keywords = norm_list(settings.genres)[:12]
    for keyword in keywords:
        params = {
            "apikey": api_key,
            "countryCode": "NL",
            "keyword": keyword,
            "size": 50,
            "sort": "date,asc",
        }
        try:
            resp = requests.get("https://app.ticketmaster.com/discovery/v2/events.json", params=params, timeout=15)
            resp.raise_for_status()
            events = resp.json().get("_embedded", {}).get("events", [])
            results.extend(parse_ticketmaster_event(e) for e in events)
        except Exception as exc:
            print(f"Ticketmaster scan failed for {keyword}: {exc}")
    unique = {x["external_id"]: x for x in results if x.get("external_id")}
    return list(unique.values())


def fetch_demo_events(settings: Setting) -> List[Dict]:
    # Demo/source templates keep the app useful before API keys and official source connectors are added.
    return [
        {"source": "Demo", "external_id": "demo:queer-techno-amsterdam", "name": "Queer Techno Night Amsterdam", "start_date": "2026-06-06", "city": "Amsterdam", "venue": "Warehouse venue", "url": "https://example.com/queer-techno", "genre_tags": "techno, queer, lgbtq", "lgbtq_branded": True, "event_type": "club", "price": 29.0},
        {"source": "Demo", "external_id": "demo:psytrance-open-air", "name": "Psytrance Open Air NL", "start_date": "2026-07-18", "city": "Utrecht", "venue": "Outdoor area", "url": "https://example.com/psytrance", "genre_tags": "psytrance, festival", "lgbtq_branded": False, "event_type": "festival", "price": 119.0},
        {"source": "Demo", "external_id": "demo:hard-techno-rave", "name": "Hard Techno Rave Rotterdam", "start_date": "2026-05-30", "city": "Rotterdam", "venue": "Club", "url": "https://example.com/hard-techno", "genre_tags": "hard techno, rave", "lgbtq_branded": False, "event_type": "club", "price": 42.0},
    ]


def upsert_event(item: Dict, settings: Setting) -> Optional[Alert]:
    item["match_score"] = score_event(item, settings)
    event = Event.query.filter_by(external_id=item["external_id"]).first()
    alert = None
    price = item.get("price")
    if event:
        old_price = event.current_price
        event.previous_price = old_price
        event.current_price = price
        event.lowest_seen_price = min([p for p in [event.lowest_seen_price, price] if p is not None], default=price)
        event.match_score = item["match_score"]
        event.last_seen_at = datetime.utcnow()
        event.name = item.get("name") or event.name
        event.url = item.get("url") or event.url
        if old_price is not None and price is not None and price < old_price:
            alert = Alert(event_id=event.id, alert_type="price_drop", message=f"Prijsdaling: {event.name} ging van €{old_price:.2f} naar €{price:.2f}.")
    else:
        event = Event(
            source=item["source"], external_id=item["external_id"], name=item["name"], start_date=item.get("start_date"),
            city=item.get("city"), venue=item.get("venue"), url=item.get("url"), genre_tags=item.get("genre_tags", ""),
            lgbtq_branded=bool(item.get("lgbtq_branded")), event_type=item.get("event_type") or infer_type(item["name"]),
            current_price=price, lowest_seen_price=price, match_score=item["match_score"])
        db.session.add(event)
        db.session.flush()
        if event.match_score >= settings.min_match_score:
            alert = Alert(event_id=event.id, alert_type="match", message=f"Nieuwe goede match ({event.match_score}/100): {event.name}.")
    if alert:
        db.session.add(alert)
    return alert


def send_email(subject: str, body: str, to_email: str) -> bool:
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or user
    if not all([host, user, password, sender, to_email]):
        print("Email not sent: SMTP settings missing")
        return False
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:
        print(f"Email send failed: {exc}")
        return False


def run_scan(send_alerts: bool = True) -> Dict[str, int]:
    with app.app_context():
        settings = get_settings()
        items = fetch_ticketmaster_events(settings)
        if not items:
            items = fetch_demo_events(settings)
        created_alerts = []
        for item in items:
            alert = upsert_event(item, settings)
            if alert:
                created_alerts.append(alert)
        db.session.commit()
        if send_alerts and created_alerts:
            lines = ["Nieuwe Event Scan Tool alerts:", ""]
            for alert in created_alerts:
                event = Event.query.get(alert.event_id)
                lines.append(f"- {alert.message}")
                lines.append(f"  {event.city or ''} | {event.start_date or ''} | score {event.match_score}/100 | prijs {event.current_price if event.current_price is not None else 'n.b.'}")
                if event.url:
                    lines.append(f"  {event.url}")
            sent = send_email("Event Scan Tool NL alerts", "\n".join(lines), settings.alert_email)
            for alert in created_alerts:
                alert.sent = bool(sent)
            db.session.commit()
        return {"events_scanned": len(items), "alerts_created": len(created_alerts)}

@app.route("/")
def index():
    settings = get_settings()
    events = Event.query.order_by(Event.match_score.desc(), Event.start_date.asc()).limit(100).all()
    alerts = Alert.query.order_by(Alert.created_at.desc()).limit(10).all()
    return render_template("index.html", events=events, alerts=alerts, settings=settings)

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    settings = get_settings()
    if request.method == "POST":
        settings.alert_email = request.form.get("alert_email", settings.alert_email)
        settings.genres = request.form.get("genres", settings.genres)
        settings.min_match_score = int(request.form.get("min_match_score", 80))
        settings.max_club_price = float(request.form.get("max_club_price", 100))
        settings.max_festival_price = float(request.form.get("max_festival_price", 800))
        settings.lgbtq_bonus = int(request.form.get("lgbtq_bonus", 10))
        settings.weekends_only = bool(request.form.get("weekends_only"))
        settings.cities = request.form.get("cities", "")
        db.session.commit()
        flash("Instellingen opgeslagen.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=settings)

@app.route("/scan", methods=["POST"])
def scan_now():
    result = run_scan(send_alerts=True)
    flash(f"Scan klaar: {result['events_scanned']} events, {result['alerts_created']} alerts.", "success")
    return redirect(url_for("index"))

@app.route("/alerts")
def alerts_page():
    alerts = Alert.query.order_by(Alert.created_at.desc()).limit(100).all()
    return render_template("alerts.html", alerts=alerts, Event=Event)


def init_db():
    with app.app_context():
        db.create_all()
        get_settings()

init_db()

scheduler = None
if os.getenv("DISABLE_SCHEDULER", "0") != "1":
    scheduler = BackgroundScheduler(timezone=os.getenv("SCAN_TIMEZONE", "Europe/Amsterdam"))
    scheduler.add_job(
        run_scan,
        "cron",
        hour=int(os.getenv("SCAN_HOUR", "10")),
        minute=int(os.getenv("SCAN_MINUTE", "0")),
        id="daily_event_scan",
        replace_existing=True,
    )
    scheduler.start()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
