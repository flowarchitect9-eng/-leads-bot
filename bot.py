import asyncio
import concurrent.futures
import csv
import io
import json
import os
import re
import sys
import time
import html
import traceback
import urllib.parse
from typing import Dict, List, Optional, Tuple, Set
import aiohttp

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    except Exception:
        pass

# ==============================================================================
# TURBO CONFIGURATION
# ==============================================================================
TELEGRAM_BOT_TOKEN = "8627892901:AAFtkiw_TgKz0C6oKE1S0wGhFNbj1z8PYjc"

CONCURRENCY_LIMIT = 400
HOMEPAGE_TIMEOUT = aiohttp.ClientTimeout(total=8.0, connect=4.0, sock_read=5.0)
SUBPAGE_TIMEOUT = aiohttp.ClientTimeout(total=6.0, connect=3.5, sock_read=4.0)
MAX_HTML_SIZE = 3 * 1024 * 1024  # 3MB max as in n8n
CONTACT_PAGES = ["/contact", "/contact-us", "/about", "/about-us"]

SOCIAL_HOSTS = {
    'facebook.com', 'fb.com', 'instagram.com', 'tiktok.com', 'youtube.com',
    'youtu.be', 'twitter.com', 'x.com', 'threads.net', 'linkedin.com',
    'pinterest.com', 'wa.me', 'whatsapp.com', 'm.me', 'messenger.com'
}

PRIORITY_HEADERS = [
    'homepage url', 'homepage', 'website', 'company website',
    'business website', 'domain', 'destination link(s)', 'destination link', 'landing page'
]
FALLBACK_HEADERS_CONTAINING = ['url', 'website', 'domain', 'link']

GENERIC_PRIORITY = [
    'info@', 'contact@', 'sales@', 'support@', 'hello@', 'admin@', 'office@',
    'inquiry@', 'inquiries@', 'enquiry@', 'help@', 'care@', 'shop@', 'order@', 'orders@'
]
FREE_MAIL_DOMAINS = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'live.com'}

US_STATE_ABBR = r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
RE_US_ADDRESS = re.compile(rf"([A-Za-z .\'-]{{2,40}}),?\s*({US_STATE_ABBR})\s*(\d{{5}})(-\d{{4}})?", re.I)
RE_STREET = re.compile(r"\d{1,6}\s+[A-Za-z0-9.\s]{3,50}?(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Suite|Ste)\.?\s*[\w#-]*$", re.I)
RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
RE_PHONE = re.compile(r"(?<![\d./])(\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]\d{3,4}[-.\s]?\d{3,4}(?![\d./])")
RE_TEL_LINK = re.compile(r'tel:([+\d][\d\s\-().]{5,18})', re.I)
RE_MAILTO = re.compile(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', re.I)
RE_CF_EMAIL = re.compile(r'data-cfemail=[\'"]([a-f0-9]+)[\'"]', re.I)

# Track active user jobs so they can be stopped on demand
ACTIVE_JOBS: Dict[int, Dict] = {}

# ==============================================================================
# FAST EXTRACTION FUNCTIONS
# ==============================================================================
def decode_cf_email(encoded: str) -> str:
    try:
        r = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i+2], 16) ^ r) for i in range(2, len(encoded), 2))
    except Exception:
        return ""

def deobfuscate_text(text: str) -> str:
    t = re.sub(r"\s*\[\s*at\s*\]\s*|\s*\(\s*at\s*\)\s*|\s+at\s+", "@", text, flags=re.I)
    return re.sub(r"\s*\[\s*dot\s*\]\s*|\s*\(\s*dot\s*\)\s*|\s+dot\s+", ".", t, flags=re.I)

def clean_text(raw: str) -> str:
    t = re.sub(r"\[[^\[\]]*\]", " ", raw)
    t = re.sub(r"(%[0-9A-Fa-f]{2})+", " ", t)
    t = re.sub(r'\{"[a-zA-Z0-9_]+":[^{}]{0,200}\}', " ", t)
    t = re.sub(r"[a-zA-Z-]+\s*:\s*[^;{}]{1,80};", " ", t)
    t = re.sub(r"\.[a-zA-Z0-9_-]+\s*\{[^}]{0,300}\}", " ", t)
    t = re.sub(r'\\+"', " ", t)
    t = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", t, flags=re.I)
    t = re.sub(r"[\t\n\r]+", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()

def get_hostname(u: str) -> str:
    try:
        host = urllib.parse.urlparse(u).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""

def is_social_host(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in SOCIAL_HOSTS)

def normalize_url(raw: str) -> str:
    u = str(raw or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u

def extract_title(html_str: str) -> str:
    m = re.search(r'property=[\'"]og:title[\'"][^>]*content=[\'"]([^\'"]{2,200})[\'"]', html_str, re.I) or \
        re.search(r'content=[\'"]([^\'"]{2,200})[\'"][^>]*property=[\'"]og:title[\'"]', html_str, re.I) or \
        re.search(r'name=[\'"]twitter:title[\'"][^>]*content=[\'"]([^\'"]{2,200})[\'"]', html_str, re.I) or \
        re.search(r'<title[^>]*>([^<]{2,200})</title>', html_str, re.I)
    if not m:
        return ""
    t = html.unescape(m.group(1))
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*[\|\-–—]\s*[^|\-–—]{1,40}$", lambda x: "" if len(t) > 60 else x.group(0), t)
    return t.strip()[:150]

def extract_description(html_str: str) -> str:
    m = re.search(r'property=[\'"]og:description[\'"][^>]*content=[\'"]([^\'"]{10,})[\'"]', html_str, re.I) or \
        re.search(r'content=[\'"]([^\'"]{10,})[\'"][^>]*property=[\'"]og:description[\'"]', html_str, re.I) or \
        re.search(r'<meta[^>]+name=[\'"]description[\'"][^>]+content=[\'"]([^\'"]{10,})[\'"]', html_str, re.I) or \
        re.search(r'<meta[^>]+content=[\'"]([^\'"]{10,})[\'"][^>]+name=[\'"]description[\'"]', html_str, re.I) or \
        re.search(r'name=[\'"]twitter:description[\'"][^>]*content=[\'"]([^\'"]{10,})[\'"]', html_str, re.I)
    if m:
        desc = html.unescape(m.group(1))
    else:
        paragraphs = re.findall(r'<p[^>]*>([^<]{30,})</p>', html_str, re.I)
        desc = ""
        for p in paragraphs[:3]:
            cleaned = clean_text(html.unescape(p))
            letters = len(re.findall(r'[a-zA-Z]', cleaned))
            if letters > 20 and (letters / max(len(cleaned), 1)) > 0.5:
                desc = cleaned
                break
    if not desc:
        return ""
    desc = clean_text(re.sub(r"<[^>]+>", " ", desc))
    desc = re.sub(r"[\x00-\x1F\x7F-\x9F]", " ", desc)
    return re.sub(r"\s+", " ", desc).strip()[:300]

def extract_meta(html_str: str) -> Dict[str, str]:
    kw_m = re.search(r'name=[\'"]keywords[\'"][^>]*content=[\'"]([^\'"]{2,300})[\'"]', html_str, re.I) or \
           re.search(r'content=[\'"]([^\'"]{2,300})[\'"][^>]*name=[\'"]keywords[\'"]', html_str, re.I)
    keywords = clean_text(html.unescape(kw_m.group(1)))[:200] if kw_m else ""

    sn_m = re.search(r'property=[\'"]og:site_name[\'"][^>]*content=[\'"]([^\'"]{1,100})[\'"]', html_str, re.I) or \
           re.search(r'content=[\'"]([^\'"]{1,100})[\'"][^>]*property=[\'"]og:site_name[\'"]', html_str, re.I)
    site_name = clean_text(html.unescape(sn_m.group(1)))[:100] if sn_m else ""

    lang_m = re.search(r'<html[^>]+lang=[\'"]([a-zA-Z\-]{2,10})[\'"]', html_str, re.I)
    language = lang_m.group(1).lower() if lang_m else ""

    return {"keywords": keywords, "site_name": site_name, "language": language}

def clean_phone(p: str) -> Optional[str]:
    digits = re.sub(r"\D", "", p)
    if len(digits) < 7 or len(digits) > 14:
        return None
    if re.match(r"^0+$", digits) or re.match(r"^(\d)\1+$", digits):
        return None
    return re.sub(r"\s+", " ", p.strip())

def extract_emails(html_str: str) -> List[str]:
    cf_emails = [decode_cf_email(m) for m in RE_CF_EMAIL.findall(html_str)]
    mailto_emails = RE_MAILTO.findall(html_str)
    deob = deobfuscate_text(html_str)
    plain_emails = RE_EMAIL.findall(deob)

    all_emails = set(cf_emails + mailto_emails + plain_emails)
    valid = []
    for e in all_emails:
        e = e.strip().lower()
        if not e or len(e) < 5:
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|css|js|pdf|woff|woff2|ttf)$", e, re.I):
            continue
        if re.match(r"^\d+x\d+", e):
            continue
        if any(bad in e for bad in ['example.com', 'sentry.io', 'wixpress.com', 'godaddy.com', 'schema.org']):
            continue
        if any(e.startswith(bad) for bad in ['test@', 'user@', 'your@', 'name@', 'email@']):
            continue
        valid.append(e)
    return list(dict.fromkeys(valid))

def extract_phones(html_str: str, existing_phones: List[str]) -> List[str]:
    tel_links = RE_TEL_LINK.findall(html_str)
    raw_phones = RE_PHONE.findall(html_str)
    raw_candidates = tel_links + [p[0] if isinstance(p, tuple) else p for p in raw_phones] + existing_phones
    cleaned = [clean_phone(p) for p in raw_candidates]
    return list(dict.fromkeys([p for p in cleaned if p]))

def parse_us_address(text: str) -> Optional[Dict[str, str]]:
    m = RE_US_ADDRESS.search(text)
    if not m:
        return None
    idx = m.start()
    street_part = text[max(0, idx - 60):idx].strip()
    sm = RE_STREET.search(street_part)
    street = sm.group(0).strip() if sm else ""
    return {
        "street": street,
        "city": m.group(1).strip(),
        "state": m.group(2).upper(),
        "zip": m.group(3),
        "country": "US"
    }

def extract_location(html_str: str, plain_text: str) -> Dict[str, str]:
    scripts = re.findall(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>', html_str, re.I | re.S)
    for s in scripts[:3]:
        try:
            data = json.loads(s.strip())
            items = data if isinstance(data, list) else (data.get("@graph", [data]) if isinstance(data, dict) else [data])
            for obj in items:
                if isinstance(obj, dict) and "address" in obj and isinstance(obj["address"], dict):
                    a = obj["address"]
                    parts = [a.get("streetAddress", ""), a.get("addressLocality", ""), a.get("addressRegion", ""), a.get("postalCode", "")]
                    return {
                        "address_found": ", ".join(filter(None, parts)),
                        "city": a.get("addressLocality", ""),
                        "state": a.get("addressRegion", ""),
                        "zip": a.get("postalCode", ""),
                        "location_source": "json-ld"
                    }
        except Exception:
            continue

    geo_place = re.search(r'name=[\'"]geo\.placename[\'"][^>]*content=[\'"]([^\'"]{2,100})[\'"]', html_str, re.I)
    geo_reg = re.search(r'name=[\'"]geo\.region[\'"][^>]*content=[\'"]([^\'"]{2,20})[\'"]', html_str, re.I)
    if geo_place or geo_reg:
        city = html.unescape(geo_place.group(1)).strip() if geo_place else ""
        state = re.sub(r'^[A-Z]{2}-', '', html.unescape(geo_reg.group(1))).strip() if geo_reg else ""
        return {"address_found": f"{city}, {state}".strip(", "), "city": city, "state": state, "zip": "", "location_source": "meta-geo"}

    addr_m = re.search(r'<address[^>]*>(.*?)</address>', html_str, re.I | re.S)
    if addr_m:
        raw_addr = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", addr_m.group(1))))
        parsed = parse_us_address(raw_addr)
        if parsed:
            parts = [parsed["street"], parsed["city"], parsed["state"], parsed["zip"]]
            return {"address_found": ", ".join(filter(None, parts)), "city": parsed["city"], "state": parsed["state"], "zip": parsed["zip"], "location_source": "address-tag"}
        if len(raw_addr) >= 5:
            return {"address_found": raw_addr[:150], "city": "", "state": "", "zip": "", "location_source": "address-tag"}

    parsed = parse_us_address(plain_text)
    if parsed:
        parts = [parsed["street"], parsed["city"], parsed["state"], parsed["zip"]]
        return {"address_found": ", ".join(filter(None, parts)), "city": parsed["city"], "state": parsed["state"], "zip": parsed["zip"], "location_source": "regex-text"}

    return {"address_found": "", "city": "", "state": "", "zip": "", "location_source": "not_found"}

def score_email(email: str, site_host: str) -> int:
    score = 0
    domain = email.split("@")[1] if "@" in email else ""
    if site_host and (domain == site_host or domain.endswith("." + site_host) or site_host.endswith("." + domain)):
        score += 100
    for idx, p in enumerate(GENERIC_PRIORITY):
        if email.startswith(p):
            score += (len(GENERIC_PRIORITY) - idx) * 5
            break
    if domain in FREE_MAIL_DOMAINS:
        score -= 20
    if re.search(r"noreply|no-reply|donotreply|tracking|newsletter", email, re.I):
        score -= 50
    return score

def pick_website_url(item: Dict[str, str]) -> Tuple[str, str, bool]:
    lower_map = {k.lower().strip(): k for k in item.keys()}
    for phfx in PRIORITY_HEADERS:
        if phfx in lower_map:
            val = normalize_url(item[lower_map[phfx]])
            if val and not is_social_host(get_hostname(val)):
                return val, lower_map[phfx], False

    candidates = [k for k in item.keys() if k.lower().strip() != 'page url' and any(f in k.lower() for f in FALLBACK_HEADERS_CONTAINING)]
    for c in candidates:
        val = normalize_url(item[c])
        if val and not is_social_host(get_hostname(val)):
            return val, c, False

    if 'page url' in lower_map:
        val = normalize_url(item[lower_map['page url']])
        if val:
            return val, lower_map['page url'], True

    return "", "", False

# ==============================================================================
# FAST ASYNC SCRAPER
# ==============================================================================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}

async def fetch_page(session: aiohttp.ClientSession, url: str, timeout_obj) -> Tuple[Optional[str], str]:
    attempts = [url]
    if url.startswith("https://"):
        attempts.append(url.replace("https://", "http://"))

    for u in attempts:
        try:
            async with session.get(u, headers=HEADERS, timeout=timeout_obj, ssl=False, allow_redirects=True) as resp:
                if resp.status < 400:
                    ct = resp.headers.get("Content-Type", "").lower()
                    if "text/html" in ct or "application/xhtml" in ct or not ct:
                        content = await resp.read()
                        if len(content) > MAX_HTML_SIZE:
                            content = content[:MAX_HTML_SIZE]
                        return content.decode("utf-8", errors="ignore"), str(resp.url)
        except Exception:
            continue
    return None, url

def discover_contact_urls(base_url: str, html_str: str) -> List[str]:
    found = []
    base_host = get_hostname(base_url)
    raw_hrefs = re.findall(r'href=[\'"]([^\'"]+)[\'"]', html_str, re.I)
    keywords = ['contact', 'about', 'team', 'staff', 'reach', 'touch', 'help', 'privacy', 'terms', 'info', 'connect']
    
    for href in raw_hrefs:
        href_lower = href.lower()
        if any(k in href_lower for k in keywords):
            if href.startswith(('javascript:', 'tel:', 'mailto:', '#', 'whatsapp:')):
                continue
            try:
                abs_url = urllib.parse.urljoin(base_url, href)
                u_host = get_hostname(abs_url)
                if (u_host == base_host or not u_host) and not is_social_host(u_host):
                    if abs_url not in found and abs_url != base_url:
                        found.append(abs_url)
            except Exception:
                pass
            if len(found) >= 4:
                break

    for p in ['/contact', '/contact-us', '/about', '/about-us']:
        try:
            abs_p = urllib.parse.urljoin(base_url, p)
            if abs_p not in found and abs_p != base_url:
                found.append(abs_p)
        except Exception:
            pass

    return found[:4]

async def process_lead(session: aiohttp.ClientSession, item: Dict[str, str]) -> Optional[Dict[str, str]]:
    url, source_col, is_social = pick_website_url(item)
    if not url or is_social:
        return None

    existing_emails = []
    existing_phones = []
    for k, val in item.items():
        lk = k.lower()
        if 'email' in lk and isinstance(val, str) and '@' in val:
            for em in RE_EMAIL.findall(val):
                existing_emails.append(em.strip().lower())
        if 'phone' in lk and isinstance(val, str) and val.strip():
            existing_phones.append(val.strip())

    html_content, final_url = await fetch_page(session, url, HOMEPAGE_TIMEOUT)
    if not html_content or len(html_content.strip()) < 10:
        return None

    if ("__cf_chl" in html_content or "cf-browser-verification" in html_content or "Just a moment..." in html_content) and len(html_content) < 12000:
        return None

    decoded = html.unescape(html_content)
    emails = extract_emails(decoded)
    emails = list(dict.fromkeys(emails + existing_emails))

    # Deep subpage scraping: check discovered /contact, /about, /team, /help links
    if not emails:
        contact_urls = discover_contact_urls(final_url, decoded)
        contact_tasks = [fetch_page(session, cu, SUBPAGE_TIMEOUT) for cu in contact_urls]
        contact_results = await asyncio.gather(*contact_tasks)
        for contact_html, _ in contact_results:
            if contact_html:
                sub_emails = extract_emails(html.unescape(contact_html))
                if sub_emails:
                    emails.extend(sub_emails)

    if not emails:
        return None

    emails = list(dict.fromkeys(emails))
    site_host = get_hostname(final_url)
    best_email = sorted(emails, key=lambda e: score_email(e, site_host), reverse=True)[0]

    phones = extract_phones(decoded, existing_phones)
    best_phone = phones[0] if phones else ""

    title = extract_title(decoded)
    description = extract_description(decoded)
    meta = extract_meta(decoded)

    clean_p = re.sub(r"<script[^>]*>.*?</script>", " ", decoded, flags=re.I | re.S)
    clean_p = re.sub(r"<style[^>]*>.*?</style>", " ", clean_p, flags=re.I | re.S)
    clean_p = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", clean_p, flags=re.I | re.S)
    clean_p = re.sub(r"<[^>]+>", " ", clean_p)
    full_clean_text = clean_text(clean_p)
    location = extract_location(decoded, full_clean_text)

    result = dict(item)
    result.update({
        "email_found": best_email,
        "all_emails_seen": "; ".join(emails),
        "phone_found": best_phone,
        "all_phones_seen": "; ".join(phones),
        "title": title,
        "description": description,
        "site_name": meta["site_name"],
        "keywords": meta["keywords"],
        "language": meta["language"],
        "address_found": location["address_found"],
        "city": location["city"],
        "state": location["state"],
        "zip": location["zip"],
        "location_source": location["location_source"],
        "text_content": full_clean_text[:500],
        "source_column_used": source_col,
        "final_url": final_url,
        "__matched_host": site_host
    })
    return result

# ==============================================================================
# TELEGRAM BOT CLIENT
# ==============================================================================
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: Optional[aiohttp.ClientSession] = None

    async def init(self):
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()

    async def send_message(self, chat_id: int, text: str, reply_markup=None, parse_mode: str = "Markdown") -> Optional[int]:
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            async with self.session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return data["result"]["message_id"]
        except Exception:
            pass
        return None

    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup=None, parse_mode: str = "Markdown"):
        try:
            url = f"{self.base_url}/editMessageText"
            payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            async with self.session.post(url, json=payload) as resp:
                pass
        except Exception:
            pass

    async def answer_callback_query(self, callback_query_id: str, text: str):
        try:
            url = f"{self.base_url}/answerCallbackQuery"
            payload = {"callback_query_id": callback_query_id, "text": text}
            async with self.session.post(url, json=payload) as resp:
                pass
        except Exception:
            pass

    async def send_document(self, chat_id: int, file_bytes: bytes, filename: str, caption: str):
        try:
            url = f"{self.base_url}/sendDocument"
            data = aiohttp.FormData()
            data.add_field('chat_id', str(chat_id))
            data.add_field('caption', caption)
            data.add_field('document', file_bytes, filename=filename, content_type='text/csv')
            async with self.session.post(url, data=data) as resp:
                pass
        except Exception:
            pass

    async def download_file(self, file_id: str) -> bytes:
        url = f"{self.base_url}/getFile?file_id={file_id}"
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise Exception(f"Telegram getFile error: {data.get('description')}")
            file_path = data["result"]["file_path"]

        download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        async with self.session.get(download_url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            return await resp.read()

# ==============================================================================
# PIPELINE EXECUTION WITH REAL-TIME SPEED & CANCEL BUTTON
# ==============================================================================
def render_progress(processed: int, total: int, found: int, start_time: float) -> str:
    pct = round((processed / total) * 100) if total > 0 else 0
    elapsed = max(time.time() - start_time, 0.001)
    rate = processed / elapsed
    eta_sec = round((total - processed) / rate) if rate > 0 else 0
    eta_text = "almost done..." if processed >= total else (f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec > 60 else f"{eta_sec}s")

    total_blocks = 12
    filled_blocks = round((pct / 100) * total_blocks)
    bar = ""
    for i in range(total_blocks):
        if i < filled_blocks:
            bar += "🟩" if i < total_blocks * 0.4 else ("🟨" if i < total_blocks * 0.75 else "🟧")
        else:
            bar += "⬜"

    stage_emoji = "🏁" if pct >= 100 else ("🔥" if pct >= 75 else ("⚡" if pct >= 50 else ("🔎" if pct >= 25 else "🚀")))
    success_rate = round((found / processed) * 100) if processed > 0 else 0
    success_emoji = "🟢" if success_rate >= 50 else ("🟡" if success_rate >= 20 else "🔴")
    speed_text = f"{rate:.1f} rows/sec" if rate > 0 else "calculating..."

    return (
        f"{stage_emoji} *LEAD ENRICHMENT — LIVE STATUS*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{bar}\n"
        f"*{pct}%* Complete\n\n"
        f"📦 *Processed:* {processed:,} / {total:,}\n"
        f"📧 *Emails Found:* {found:,}\n"
        f"{success_emoji} *Success Rate:* {success_rate}%\n"
        f"⚡ *Realtime Speed:* {speed_text}\n"
        f"⏱ *ETA:* {eta_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 _Click button below to stop anytime & download_"
    )

def build_result_csv(enriched_results: List[Dict[str, str]]) -> bytes:
    seen_emails: Set[str] = set()
    seen_hosts: Set[str] = set()
    final_leads: List[Dict[str, str]] = []

    for item in enriched_results:
        email = (item.get("email_found") or "").strip().lower()
        host = item.get("__matched_host") or ""
        if email and email in seen_emails:
            continue
        if host and host in seen_hosts:
            continue
        if email:
            seen_emails.add(email)
        if host:
            seen_hosts.add(host)

        clean_item = dict(item)
        clean_item.pop("__matched_host", None)
        final_leads.append(clean_item)

    if not final_leads:
        return b""

    PREFERRED_ORDER = [
        'email_found', 'all_emails_seen', 'phone_found', 'all_phones_seen',
        'title', 'description', 'site_name', 'keywords', 'language',
        'address_found', 'city', 'state', 'zip', 'location_source', 'text_content',
        'source_column_used', 'final_url'
    ]
    all_keys = list(final_leads[0].keys())
    ordered_headers = [k for k in PREFERRED_ORDER if k in all_keys] + [k for k in all_keys if k not in PREFERRED_ORDER]

    out_stream = io.StringIO()
    writer = csv.DictWriter(out_stream, fieldnames=ordered_headers, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(final_leads)
    return out_stream.getvalue().encode("utf-8")

async def handle_csv_enrichment(bot: TelegramBot, chat_id: int, file_id: str, file_name: str):
    stop_button_markup = {
        "inline_keyboard": [
            [{"text": "🛑 Stop & Download Current Leads", "callback_data": f"stop_{chat_id}"}]
        ]
    }

    status_msg_id = await bot.send_message(chat_id, "🔎 *Got your file — launching turbo enrichment engine...*\n\n░░░░░░░░░░░░░░░░░░░░ 0%", reply_markup=stop_button_markup)
    if not status_msg_id:
        return

    try:
        csv_bytes = await bot.download_file(file_id)
        try:
            csv_text = csv_bytes.decode("utf-8-sig", errors="ignore")
        except Exception:
            csv_text = csv_bytes.decode("latin-1", errors="ignore")

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = [r for r in reader]
        if not rows:
            await bot.edit_message(chat_id, status_msg_id, "❌ *Error:* The uploaded CSV file is empty!")
            return

        total_rows = min(len(rows), 50000)
        rows = rows[:total_rows]

        has_website_col = any(
            any(k in col.lower() for k in ['website', 'url', 'domain', 'link'])
            for col in rows[0].keys()
        )
        if not has_website_col:
            await bot.edit_message(chat_id, status_msg_id, "❌ *Error:* No `website` / `url` / `domain` column found in your CSV file!")
            return

        queue = asyncio.Queue()
        for item in rows:
            queue.put_nowait(item)

        job_data = {
            "is_stopped": False,
            "processed_count": 0,
            "found_count": 0,
            "start_time": time.time(),
            "total_rows": total_rows,
            "enriched_results": [],
            "status_msg_id": status_msg_id
        }
        ACTIVE_JOBS[chat_id] = job_data

        stop_updater = False

        async def live_progress_loop():
            last_text = ""
            while not stop_updater and not job_data["is_stopped"]:
                await asyncio.sleep(1.8)
                text = render_progress(job_data["processed_count"], total_rows, job_data["found_count"], job_data["start_time"])
                if text != last_text:
                    await bot.edit_message(chat_id, status_msg_id, text, reply_markup=stop_button_markup)
                    last_text = text

        updater_task = asyncio.create_task(live_progress_loop())

        conn = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT, limit_per_host=8, ssl=False, ttl_dns_cache=600, force_close=False)
        
        async with aiohttp.ClientSession(connector=conn) as scraper_session:
            async def queue_worker():
                while not queue.empty() and not job_data["is_stopped"]:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        res = await process_lead(scraper_session, item)
                        if res:
                            job_data["found_count"] += 1
                            job_data["enriched_results"].append(res)
                    except Exception:
                        pass
                    finally:
                        job_data["processed_count"] += 1
                        queue.task_done()

            workers = [asyncio.create_task(queue_worker()) for _ in range(CONCURRENCY_LIMIT)]
            await asyncio.gather(*workers)

        stop_updater = True
        updater_task.cancel()

        was_stopped = job_data["is_stopped"]
        ACTIVE_JOBS.pop(chat_id, None)

        final_csv_bytes = build_result_csv(job_data["enriched_results"])
        if not final_csv_bytes:
            await bot.edit_message(chat_id, status_msg_id, "⚠️ Enrichment finished, but no valid contact emails were found.")
            return

        elapsed = max(time.time() - job_data["start_time"], 0.001)
        elapsed_text = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed > 60 else f"{int(elapsed)}s"
        final_count = len(job_data["enriched_results"])
        avg_speed = f"{(job_data['processed_count'] / elapsed):.1f}"

        status_title = "🛑 *ENRICHMENT STOPPED (EARLY OUTPUT)*" if was_stopped else "✅ *ENRICHMENT COMPLETE!*"
        summary_text = (
            f"{status_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📄 *Leads Generated:* {final_count:,} (deduped)\n"
            f"🔎 *Scanned:* {job_data['processed_count']:,} / {total_rows:,} rows\n"
            f"⚡ *Avg Speed:* {avg_speed} rows/sec\n"
            f"⏱ *Total Time:* {elapsed_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📎 *Enriched file attached below* 👇"
        )

        await bot.edit_message(chat_id, status_msg_id, summary_text)
        await bot.send_document(chat_id, final_csv_bytes, "leads-enriched.csv", caption="leads-enriched.csv")

    except Exception as e:
        traceback.print_exc()
        await bot.edit_message(chat_id, status_msg_id, f"❌ *Error processing file:* `{str(e)[:200]}`")

# ==============================================================================
# OWNER & PUBLIC ACCESS CONTROL SYSTEM
# ==============================================================================
IS_PUBLIC_ACTIVE = True
ADMIN_USER_IDS: Set[int] = set()

def get_admin_panel_markup() -> dict:
    btn_text = "🔴 Turn OFF for Public" if IS_PUBLIC_ACTIVE else "🟢 Turn ON for Public"
    btn_data = "toggle_public_off" if IS_PUBLIC_ACTIVE else "toggle_public_on"
    return {
        "inline_keyboard": [
            [{"text": btn_text, "callback_data": btn_data}],
            [{"text": "🔄 Refresh Status", "callback_data": "refresh_admin"}]
        ]
    }

def get_admin_panel_text() -> str:
    status_emoji = "🟢 *PUBLIC (ACTIVE)*" if IS_PUBLIC_ACTIVE else "🔴 *OFF (MAINTENANCE MODE)*"
    return (
        "👑 *OWNER CONTROL PANEL*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 *Bot Access Status:* {status_emoji}\n"
        f"📊 *Running Jobs:* {len(ACTIVE_JOBS)}\n\n"
        "_Use the button below to toggle public access on or off anytime:_"
    )

MAINTENANCE_MESSAGE = (
    "🛠️ *BOT UNDER MAINTENANCE*\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "The Lead Enrichment Bot is currently *OFF* for maintenance by the owner.\n\n"
    "Please try again later. Thank you for your patience! 🙏"
)

WELCOME_MESSAGE = (
    "🚀 *High-Speed Lead Enrichment Bot*\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "Welcome! Send me any `.csv` file containing websites or domains, and I will extract:\n\n"
    "📧 *Verified Emails* (Priority scored)\n"
    "📞 *Phone Numbers*\n"
    "📍 *Physical Addresses / Location*\n"
    "🌐 *Title, Description & Keywords*\n\n"
    "⚡ *Features:*\n"
    "• Up to 50,000 leads supported\n"
    "• Ultra Turbo speed with live rows/sec\n"
    "• 🛑 Stop & Download button anytime\n\n"
    "👉 *Simply attach & send your `.csv` file now!*"
)

def run_sync_http_server(port):
    import http.server
    import socketserver
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Lead Enricher Bot is Live and Healthy!")
        def log_message(self, format, *args):
            pass
    try:
        with socketserver.TCPServer(("0.0.0.0", port), HealthHandler) as httpd:
            print(f"[RENDER HEALTH] Web server successfully bound to 0.0.0.0:{port}", flush=True)
            httpd.serve_forever()
    except Exception as e:
        print(f"[RENDER HEALTH] Server on port {port} error: {e}", flush=True)

def start_health_thread():
    import threading
    port = int(os.environ.get("PORT", 10000))
    t = threading.Thread(target=run_sync_http_server, args=(port,), daemon=True)
    t.start()

async def main():
    global IS_PUBLIC_ACTIVE
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=500)
    loop.set_default_executor(executor)

    try:
        start_health_thread()
    except Exception as e:
        print(f"Web server notice: {e}")

    bot = TelegramBot(TELEGRAM_BOT_TOKEN)
    await bot.init()
    print("Turbo Lead Enricher Bot is running with Live Speed & Stop Button...")
    print("Waiting for CSV files on Telegram (@alif_support_alert_bot)...")

    offset = 0
    while True:
        try:
            url = f"{bot.base_url}/getUpdates?offset={offset}&timeout=30"
            async with bot.session.get(url, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1

                        # Handle Callback Query (Buttons)
                        callback = update.get("callback_query")
                        if callback:
                            cb_id = callback["id"]
                            cb_data = callback.get("data", "")
                            from_user_id = callback.get("from", {}).get("id")
                            cb_msg = callback.get("message")
                            cb_chat_id = cb_msg["chat"]["id"] if cb_msg else from_user_id
                            cb_msg_id = cb_msg["message_id"] if cb_msg else None

                            if cb_data.startswith("stop_"):
                                target_chat_id = int(cb_data.split("_")[1])
                                if target_chat_id in ACTIVE_JOBS:
                                    ACTIVE_JOBS[target_chat_id]["is_stopped"] = True
                                    await bot.answer_callback_query(cb_id, "Stopping and preparing CSV file...")
                                else:
                                    await bot.answer_callback_query(cb_id, "Job is not active.")
                            elif cb_data == "toggle_public_off":
                                ADMIN_USER_IDS.add(from_user_id)
                                IS_PUBLIC_ACTIVE = False
                                await bot.answer_callback_query(cb_id, "🔴 Bot is now OFF for Public!")
                                if cb_msg_id:
                                    await bot.edit_message(cb_chat_id, cb_msg_id, get_admin_panel_text(), reply_markup=get_admin_panel_markup())
                            elif cb_data == "toggle_public_on":
                                ADMIN_USER_IDS.add(from_user_id)
                                IS_PUBLIC_ACTIVE = True
                                await bot.answer_callback_query(cb_id, "🟢 Bot is now ON for Public!")
                                if cb_msg_id:
                                    await bot.edit_message(cb_chat_id, cb_msg_id, get_admin_panel_text(), reply_markup=get_admin_panel_markup())
                            elif cb_data == "refresh_admin":
                                ADMIN_USER_IDS.add(from_user_id)
                                await bot.answer_callback_query(cb_id, "Updated!")
                                if cb_msg_id:
                                    await bot.edit_message(cb_chat_id, cb_msg_id, get_admin_panel_text(), reply_markup=get_admin_panel_markup())
                            continue

                        message = update.get("message")
                        if not message:
                            continue

                        chat_id = message["chat"]["id"]
                        user_id = message.get("from", {}).get("id", chat_id)
                        text = (message.get("text") or "").strip()
                        document = message.get("document")

                        # Admin Commands
                        if text.lower() in ["/admin", "/owner", "/panel", "/toggle", "/status"]:
                            ADMIN_USER_IDS.add(user_id)
                            await bot.send_message(chat_id, get_admin_panel_text(), reply_markup=get_admin_panel_markup())
                            continue
                        elif text.lower() == "/on":
                            ADMIN_USER_IDS.add(user_id)
                            IS_PUBLIC_ACTIVE = True
                            await bot.send_message(chat_id, "🟢 *Bot is now ON for the public!*", reply_markup=get_admin_panel_markup())
                            continue
                        elif text.lower() == "/off":
                            ADMIN_USER_IDS.add(user_id)
                            IS_PUBLIC_ACTIVE = False
                            await bot.send_message(chat_id, "🔴 *Bot is now OFF (Maintenance Mode) for the public!*", reply_markup=get_admin_panel_markup())
                            continue

                        # Check Maintenance Mode for non-admins
                        is_admin = (user_id in ADMIN_USER_IDS)
                        if not IS_PUBLIC_ACTIVE and not is_admin:
                            await bot.send_message(chat_id, MAINTENANCE_MESSAGE)
                            continue

                        # File Enrichment
                        if document:
                            file_name = document.get("file_name", "").lower()
                            if file_name.endswith(".csv"):
                                asyncio.create_task(handle_csv_enrichment(bot, chat_id, document["file_id"], file_name))
                            else:
                                await bot.send_message(chat_id, "❌ *Please upload a valid `.csv` file* with a website/url column.")
                        elif text:
                            await bot.send_message(chat_id, WELCOME_MESSAGE)
        except Exception:
            await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.")
