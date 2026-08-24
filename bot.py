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
import socket
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
# CONFIGURATION & HIGH PERFORMANCE SETTINGS
# ==============================================================================
TELEGRAM_BOT_TOKEN = "8627892901:AAFtkiw_TgKz0C6oKE1S0wGhFNbj1z8PYjc"
CONCURRENCY_LIMIT = 60

HOMEPAGE_TIMEOUT = aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=6.0)
SUBPAGE_TIMEOUT = aiohttp.ClientTimeout(total=7.0, connect=3.0, sock_read=4.5)
SEARCH_TIMEOUT = aiohttp.ClientTimeout(total=5.0, connect=2.5, sock_read=3.0)
MAX_HTML_SIZE = 2 * 1024 * 1024

SEARCH_OR_MAPS_HOSTS = {
    'google.com', 'google.co', 'goo.gl', 'maps.google.com', 'googleusercontent.com',
    'bing.com', 'yahoo.com', 'duckduckgo.com', 'baidu.com', 'yandex.com'
}

SOCIAL_HOSTS = {
    'facebook.com', 'fb.com', 'instagram.com', 'tiktok.com', 'youtube.com',
    'youtu.be', 'twitter.com', 'x.com', 'threads.net', 'linkedin.com',
    'pinterest.com', 'wa.me', 'whatsapp.com', 'm.me', 'messenger.com', 'yelp.com'
}

PRIORITY_HEADERS = [
    'homepage url', 'homepage', 'website', 'company website',
    'business website', 'domain', 'destination link(s)', 'destination link', 'landing page'
]

GENERIC_PRIORITY = [
    'info@', 'contact@', 'sales@', 'support@', 'hello@', 'admin@', 'office@',
    'inquiry@', 'inquiries@', 'enquiry@', 'help@', 'care@', 'shop@', 'order@', 'orders@'
]
FREE_MAIL_DOMAINS = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'live.com', 'aol.com'}

US_STATE_ABBR = r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
RE_US_ADDRESS = re.compile(rf"([A-Za-z .\'-]{{2,40}}),?\s*({US_STATE_ABBR})\s*(\d{{5}})(-\d{{4}})?", re.I)
RE_STREET = re.compile(r"\d{1,6}\s+[A-Za-z0-9.\s]{3,50}?(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Suite|Ste)\.?\s*[\w#-]*$", re.I)
RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
RE_PHONE = re.compile(r"(?<![\d./])(\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]\d{3,4}[-.\s]?\d{3,4}(?![\d./])")
RE_TEL_LINK = re.compile(r'tel:([+\d][\d\s\-().]{5,18})', re.I)
RE_MAILTO = re.compile(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', re.I)
RE_CF_EMAIL = re.compile(r'data-cfemail=[\'"]([a-f0-9]+)[\'"]', re.I)

ACTIVE_JOBS: Dict[int, Dict] = {}
IS_PUBLIC_ACTIVE = True
ADMIN_USER_IDS: Set[int] = set()
MX_CACHE: Dict[str, bool] = {}

# ==============================================================================
# HELPER DECODERS & PARSERS
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
    t = raw
    t = re.sub(r"\[[^\[\]]*\]", " ", t)
    t = re.sub(r"(%[0-9A-Fa-f]{2})+", " ", t)
    t = re.sub(r"%[0-9A-Fa-f]{2}", " ", t)
    t = re.sub(r'\{"[a-zA-Z0-9_]+":[^{}]{0,200}\}', " ", t)
    t = re.sub(r"[a-zA-Z-]+\s*:\s*[^;{}]{1,80};", " ", t)
    t = re.sub(r"\.[a-zA-Z0-9_-]+\s*\{[^}]{0,300}\}", " ", t)
    t = re.sub(r'\\+"', " ", t)
    t = re.sub(r"&quot;|&amp;|&#\d+;", " ", t)
    t = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", t, flags=re.I)
    t = re.sub(r"[\t\n\r]+", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()

def get_hostname(u: str) -> str:
    try:
        host = urllib.parse.urlparse(u).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""

def is_search_or_maps(host: str) -> bool:
    if not host:
        return True
    return any(host == s or host.endswith('.' + s) for s in SEARCH_OR_MAPS_HOSTS)

def is_social_host(host: str) -> bool:
    if not host:
        return False
    return any(host == s or host.endswith('.' + s) for s in SOCIAL_HOSTS)

def normalize_url(raw: str) -> str:
    u = str(raw or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u

def extract_real_target_url(raw_val: str) -> str:
    v = str(raw_val or "").strip()
    if not v:
        return ""
    if 'google.' in v and 'adurl=' in v:
        try:
            parsed = urllib.parse.urlparse(v)
            qs = urllib.parse.parse_qs(parsed.query)
            if 'adurl' in qs and qs['adurl']:
                target = qs['adurl'][0]
                if not is_search_or_maps(get_hostname(target)) and not is_social_host(get_hostname(target)):
                    return normalize_url(target)
        except Exception:
            pass
    if re.match(r"^https?://", v, re.I) or v.startswith("www.") or re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/?", v):
        norm = normalize_url(v)
        host = get_hostname(norm)
        if not is_search_or_maps(host) and not is_social_host(host):
            return norm
    return ""

def pick_website_url(item: Dict[str, str]) -> Tuple[str, str, bool]:
    lower_map = {k.lower().strip(): k for k in item.keys()}
    for phfx in PRIORITY_HEADERS:
        if phfx in lower_map:
            val = extract_real_target_url(item[lower_map[phfx]])
            if val:
                return val, lower_map[phfx], False

    for k, raw_v in item.items():
        lk = k.lower().strip()
        if 'page url' in lk or 'profile' in lk:
            continue
        val = extract_real_target_url(raw_v)
        if val:
            return val, k, False

    if 'page url' in lower_map:
        val = normalize_url(item[lower_map['page url']])
        if val:
            return val, lower_map['page url'], True

    return "", "", False

def score_email_candidate(email_cand: Tuple[str, str, int], site_host: str) -> int:
    email, source, base_score = email_cand
    score = base_score
    domain = email.split("@")[1] if "@" in email else ""
    if site_host and (domain == site_host or domain.endswith("." + site_host) or site_host.endswith("." + domain)):
        score += 150
    for idx, p in enumerate(GENERIC_PRIORITY):
        if email.startswith(p):
            score += (len(GENERIC_PRIORITY) - idx) * 10
            break
    if domain in FREE_MAIL_DOMAINS:
        score -= 40
    if re.search(r"noreply|no-reply|donotreply|tracking|newsletter", email, re.I):
        score -= 100
    return score

def extract_emails_from_html(decoded: str) -> List[str]:
    cf_emails = [decode_cf_email(m) for m in RE_CF_EMAIL.findall(decoded)]
    mailto_emails = RE_MAILTO.findall(decoded)
    deob = deobfuscate_text(decoded)
    plain_emails = RE_EMAIL.findall(deob)

    all_emails = cf_emails + mailto_emails + plain_emails
    valid = []
    for e in all_emails:
        e = e.strip().lower()
        if not e or len(e) < 5 or not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", e):
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|css|js|pdf|woff|woff2|ttf)$", e, re.I):
            continue
        if re.match(r"^\d+x\d+", e):
            continue
        if any(bad in e for bad in ['example.com', 'sentry.io', 'wixpress.com', 'godaddy.com', 'schema.org', 'gravatar.com']):
            continue
        if any(e.startswith(bad) for bad in ['test@', 'user@', 'your@', 'name@', 'email@']):
            continue
        valid.append(e)
    return list(dict.fromkeys(valid))

def extract_phones(decoded: str, existing_phones: List[str]) -> List[str]:
    tel_links = [m.strip() for m in RE_TEL_LINK.findall(decoded)]
    raw_phones = [p[0] if isinstance(p, tuple) else p for p in RE_PHONE.findall(decoded)]
    all_cands = tel_links + raw_phones + existing_phones
    cleaned = []
    for p in all_cands:
        digits = re.sub(r"\D", "", p)
        if 7 <= len(digits) <= 14 and not re.match(r"^0+$", digits) and not re.match(r"^(\d)\1+$", digits):
            cleaned.append(re.sub(r"\s+", " ", p.strip()))
    return list(dict.fromkeys(cleaned))

def extract_location(html_str: str, page_text: str) -> Dict[str, str]:
    scripts = re.findall(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>', html_str, re.I | re.S)
    for s in scripts:
        try:
            data = json.loads(s.strip())
            items = data if isinstance(data, list) else (data.get("@graph", [data]) if isinstance(data, dict) else [data])
            for obj in items:
                if isinstance(obj, dict) and "address" in obj and isinstance(obj["address"], dict):
                    a = obj["address"]
                    parts = [a.get("streetAddress", ""), a.get("addressLocality", ""), a.get("addressRegion", ""), a.get("postalCode", "")]
                    addr_found = ", ".join(filter(None, parts))
                    if addr_found:
                        return {
                            "address_found": addr_found,
                            "city": a.get("addressLocality", ""),
                            "state": a.get("addressRegion", ""),
                            "zip": a.get("postalCode", ""),
                            "location_source": "json-ld"
                        }
        except Exception:
            continue

    placename = re.search(r'name=[\'"]geo\.placename[\'"][^>]*content=[\'"]([^\'"]{2,100})[\'"]', html_str, re.I)
    region = re.search(r'name=[\'"]geo\.region[\'"][^>]*content=[\'"]([^\'"]{2,20})[\'"]', html_str, re.I)
    if placename or region:
        city = html.unescape(placename.group(1)).strip() if placename else ""
        state = re.sub(r'^[A-Z]{2}-', '', html.unescape(region.group(1))).strip() if region else ""
        return {"address_found": f"{city}, {state}".strip(", "), "city": city, "state": state, "zip": "", "location_source": "meta-geo"}

    m = RE_US_ADDRESS.search(page_text)
    if m:
        city_idx = m.start()
        street_part = page_text[max(0, city_idx - 60):city_idx].strip()
        sm = RE_STREET.search(street_part)
        street = sm.group(0).strip() if sm else ""
        city, state, zip_c = m.group(1).strip(), m.group(2).upper(), m.group(3)
        parts = [street, city, state, zip_c]
        return {"address_found": ", ".join(filter(None, parts)), "city": city, "state": state, "zip": zip_c, "location_source": "regex-text"}

    return {"address_found": "", "city": "", "state": "", "zip": "", "location_source": "not_found"}

def discover_contact_urls(base_url: str, html_str: str) -> List[str]:
    found = []
    base_host = get_hostname(base_url)
    raw_hrefs = re.findall(r'href=[\'"]([^\'"]+)[\'"]', html_str, re.I)
    keywords = ['contact', 'about', 'team', 'staff', 'reach', 'touch', 'help', 'privacy', 'terms', 'info', 'connect', 'quote', 'broker', 'agent']

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
            if len(found) >= 6:
                break

    for p in ['/contact', '/contact-us', '/about', '/about-us', '/our-team', '/privacy-policy']:
        try:
            abs_p = urllib.parse.urljoin(base_url, p)
            if abs_p not in found and abs_p != base_url:
                found.append(abs_p)
        except Exception:
            pass

    return found[:6]

# ==============================================================================
# FAST ASYNC SCRAPER ENGINE
# ==============================================================================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}

async def fetch_page(session: aiohttp.ClientSession, url: str, timeout_obj) -> Tuple[Optional[str], str, str]:
    """Returns (html_content, final_url, status_diag)"""
    attempts = [url]
    if url.startswith("https://"):
        attempts.append(url.replace("https://", "http://"))

    last_diag = "CONNECTION_FAILED"
    for u in attempts:
        try:
            async with session.get(u, headers=HEADERS, timeout=timeout_obj, ssl=False, allow_redirects=True) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("Content-Type", "").lower()
                    if "text/html" in ct or "application/xhtml" in ct or not ct:
                        content = await resp.read()
                        if len(content) > MAX_HTML_SIZE:
                            content = content[:MAX_HTML_SIZE]
                        return content.decode("utf-8", errors="ignore"), str(resp.url), "SUCCESS"
                elif resp.status in (403, 503):
                    last_diag = "BLOCKED_CLOUDFLARE_403"
                else:
                    last_diag = f"HTTP_{resp.status}"
        except asyncio.TimeoutError:
            last_diag = "TIMEOUT"
        except Exception as e:
            if "getaddrinfo" in str(e).lower() or "dns" in str(e).lower():
                last_diag = "DEAD_EXPIRED_DOMAIN"
            else:
                last_diag = "CONNECTION_FAILED"
    return None, url, last_diag

async def fetch_social_page_emails(session: aiohttp.ClientSession, social_url: str) -> List[Tuple[str, str, int]]:
    if not social_url or not social_url.startswith('http'):
        return []
    try:
        async with session.get(social_url, headers=HEADERS, timeout=SUBPAGE_TIMEOUT, ssl=False) as resp:
            if resp.status < 400:
                body = await resp.text(errors='ignore')
                emails = extract_emails_from_html(html.unescape(body))
                return [(e, f"Scraped Social: {social_url}", 1050) for e in emails]
    except Exception:
        pass
    return []

async def fetch_search_fallback(session: aiohttp.ClientSession, domain: str, comp_name: str) -> List[Tuple[str, str, int]]:
    results = []
    query = f"site:{domain} email" if domain else f'"{comp_name}" email contact'
    search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    try:
        async with session.get(search_url, headers=HEADERS, timeout=SEARCH_TIMEOUT, ssl=False) as resp:
            if resp.status == 200:
                body = await resp.text(errors='ignore')
                found = extract_emails_from_html(html.unescape(body))
                for fe in found:
                    results.append((fe, f"Search Cache: {query}", 800))
    except Exception:
        pass
    return results

def extract_company_name_from_item(item: Dict[str, str]) -> str:
    for k, v in item.items():
        lk = k.lower().strip()
        val = str(v or "").strip()
        if not val:
            continue
        if any(w in lk for w in ['company', 'business name', 'title', 'qbf1pd']):
            if not val.startswith(('http', 'www', '{')):
                return val
    return ""

async def process_lead(session: aiohttp.ClientSession, item: Dict[str, str], counters: Dict) -> Dict[str, str]:
    url, source_col, is_social = pick_website_url(item)
    
    # 1. Check Incoming CSV Emails & Phones
    email_candidates: List[Tuple[str, str, int]] = []
    existing_phones = []
    for k, val in item.items():
        lk = k.lower()
        if 'email' in lk and isinstance(val, str) and '@' in val:
            for em in RE_EMAIL.findall(val):
                email_candidates.append((em.strip().lower(), f"Input CSV [{k}]", 1000))
        if 'phone' in lk and isinstance(val, str) and val.strip():
            existing_phones.append(val.strip())

    site_host = get_hostname(url) if url else ""
    if not site_host:
        comp_name = extract_company_name_from_item(item)
        if comp_name:
            clean_dom = re.sub(r'[^a-zA-Z0-9]', '', comp_name).lower()
            if len(clean_dom) >= 3:
                site_host = clean_dom + ".com"
                url = "https://" + site_host

    if not url:
        counters["no_url"] += 1
        return {
            **item,
            "email_found": "",
            "email_source": "",
            "email_status": "NO_WEBSITE",
            "website_issue": "NO_WEBSITE_PROVIDED",
            "all_emails_seen": "",
            "phone_found": existing_phones[0] if existing_phones else "",
            "all_phones_seen": "; ".join(existing_phones),
            "title": "", "description": "", "site_name": "", "keywords": "", "language": "",
            "address_found": "", "city": "", "state": "", "zip": "", "location_source": "not_found",
            "facebook_url": "", "instagram_url": "", "linkedin_url": "", "twitter_url": "",
            "text_content": "", "source_column_used": "", "final_url": ""
        }

    html_content, final_url, status_diag = await fetch_page(session, url, HOMEPAGE_TIMEOUT)

    decoded = ""
    title = ""
    description = ""
    site_name = ""
    keywords = ""
    language = ""
    location = {"address_found": "", "city": "", "state": "", "zip": "", "location_source": "not_found"}
    phones = list(dict.fromkeys(existing_phones))
    facebook_url = ""
    instagram_url = ""
    linkedin_url = ""
    twitter_url = ""
    full_clean_text = ""
    website_issue = "LIVE"

    if html_content and len(html_content.strip()) >= 10:
        decoded = html.unescape(html_content)
        scraped_emails = extract_emails_from_html(decoded)
        for se in scraped_emails:
            email_candidates.append((se, f"Scraped Homepage: {final_url}", 1200))

        # Deep Subpage Crawling (contact / about / team)
        if not email_candidates:
            contact_urls = discover_contact_urls(final_url, decoded)
            contact_tasks = [fetch_page(session, cu, SUBPAGE_TIMEOUT) for cu in contact_urls]
            contact_results = await asyncio.gather(*contact_tasks)
            for contact_html, sub_url, _ in contact_results:
                if contact_html:
                    sub_emails = extract_emails_from_html(html.unescape(contact_html))
                    for sube in sub_emails:
                        email_candidates.append((sube, f"Scraped Subpage: {sub_url}", 1100))

        # Social Profiles
        fb_m = re.search(r'href=[\'"](https?://(?:www\.)?(?:facebook\.com|fb\.me)/[^\'"]+)[\'"]', decoded, re.I)
        ig_m = re.search(r'href=[\'"](https?://(?:www\.)?instagram\.com/[^\'"]+)[\'"]', decoded, re.I)
        li_m = re.search(r'href=[\'"](https?://(?:www\.)?linkedin\.com/(?:company|in)/[^\'"]+)[\'"]', decoded, re.I)
        tw_m = re.search(r'href=[\'"](https?://(?:www\.)?(?:twitter\.com|x\.com)/[^\'"]+)[\'"]', decoded, re.I)

        facebook_url = fb_m.group(1).split('?')[0] if fb_m else ""
        instagram_url = ig_m.group(1).split('?')[0] if ig_m else ""
        linkedin_url = li_m.group(1).split('?')[0] if li_m else ""
        twitter_url = tw_m.group(1).split('?')[0] if tw_m else ""

        phones = extract_phones(decoded, existing_phones)

        m_og = re.search(r'property=[\'"]og:title[\'"][^>]*content=[\'"]([^\'"]{2,200})[\'"]', decoded, re.I)
        m_t = re.search(r'<title[^>]*>([^<]{2,200})</title>', decoded, re.I)
        title = clean_text(html.unescape(m_og.group(1) if m_og else (m_t.group(1) if m_t else "")))[:150]

        og_desc = re.search(r'property=[\'"]og:description[\'"][^>]*content=[\'"]([^\'"]{10,})[\'"]', decoded, re.I)
        m_desc = re.search(r'<meta[^>]+name=[\'"]description[\'"][^>]+content=[\'"]([^\'"]{10,})[\'"]', decoded, re.I)
        description = clean_text(html.unescape(og_desc.group(1) if og_desc else (m_desc.group(1) if m_desc else "")))[:300]

        kw = re.search(r'name=[\'"]keywords[\'"][^>]*content=[\'"]([^\'"]{2,300})[\'"]', decoded, re.I)
        keywords = clean_text(html.unescape(kw.group(1)))[:200] if kw else ""

        sn = re.search(r'property=[\'"]og:site_name[\'"][^>]*content=[\'"]([^\'"]{1,100})[\'"]', decoded, re.I)
        site_name = clean_text(html.unescape(sn.group(1)))[:100] if sn else ""

        lang = re.search(r'<html[^>]+lang=[\'"]([a-zA-Z\-]{2,10})[\'"]', decoded, re.I)
        language = lang.group(1).lower() if lang else ""

        clean_p = re.sub(r"<script[^>]*>.*?</script>", " ", decoded, flags=re.I | re.S)
        clean_p = re.sub(r"<style[^>]*>.*?</style>", " ", clean_p, flags=re.I | re.S)
        clean_p = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", clean_p, flags=re.I | re.S)
        clean_p = re.sub(r"<[^>]+>", " ", clean_p)
        full_clean_text = clean_text(clean_p)
        location = extract_location(decoded, full_clean_text)

        if not email_candidates:
            website_issue = "CONTACT_FORM_ONLY"
            counters["contact_form"] += 1
    else:
        if status_diag == "BLOCKED_CLOUDFLARE_403":
            website_issue = "BLOCKED_CLOUDFLARE_CAPTCHA"
            counters["blocked"] += 1
        elif status_diag == "DEAD_EXPIRED_DOMAIN":
            website_issue = "DEAD_EXPIRED_DOMAIN"
            counters["dead_domain"] += 1
        else:
            website_issue = f"FAILED_{status_diag}"
            counters["dead_domain"] += 1

    # 4. Social Page Scraping Fallback
    if not email_candidates and (facebook_url or instagram_url):
        target_social = facebook_url or instagram_url
        soc_emails = await fetch_social_page_emails(session, target_social)
        if soc_emails:
            email_candidates.extend(soc_emails)

    # 5. Search Engine Cache Fallback
    if not email_candidates and site_host:
        comp_n = extract_company_name_from_item(item)
        search_res = await fetch_search_fallback(session, site_host, comp_n)
        if search_res:
            email_candidates.extend(search_res)

    best_email = ""
    best_source = ""
    email_status = "NOT_FOUND"
    all_seen = ""

    if email_candidates:
        unique_candidates: Dict[str, Tuple[str, str, int]] = {}
        for cand in email_candidates:
            em = cand[0].lower().strip()
            if em not in unique_candidates:
                unique_candidates[em] = cand

        scored = sorted(unique_candidates.values(), key=lambda c: score_email_candidate(c, site_host), reverse=True)
        best_cand = scored[0]
        best_email = best_cand[0]
        best_source = best_cand[1]
        email_status = "VERIFIED_SCRAPED" if "Scraped" in best_source else ("INPUT_CSV" if "CSV" in best_source else "SEARCH_CACHE")
        all_seen = "; ".join([c[0] for c in scored])
        website_issue = "LIVE_WITH_EMAIL"

    best_phone = phones[0] if phones else ""
    if best_phone:
        counters["phones"] += 1
    if location["address_found"]:
        counters["addresses"] += 1

    result = dict(item)
    result.update({
        "email_found": best_email,
        "email_source": best_source,
        "email_status": email_status,
        "website_issue": website_issue,
        "all_emails_seen": all_seen,
        "phone_found": best_phone,
        "all_phones_seen": "; ".join(phones),
        "title": title,
        "description": description,
        "site_name": site_name,
        "keywords": keywords,
        "language": language,
        "address_found": location["address_found"],
        "city": location["city"],
        "state": location["state"],
        "zip": location["zip"],
        "location_source": location["location_source"],
        "facebook_url": facebook_url,
        "instagram_url": instagram_url,
        "linkedin_url": linkedin_url,
        "twitter_url": twitter_url,
        "text_content": full_clean_text[:500],
        "source_column_used": source_col,
        "final_url": final_url,
        "__matched_host": site_host
    })
    return result

# ==============================================================================
# TELEGRAM BOT CLIENT & DASHBOARD
# ==============================================================================
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: Optional[aiohttp.ClientSession] = None

    async def init(self):
        if not self.session:
            self.session = aiohttp.ClientSession()

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

def render_progress_diagnostics(job_data: Dict) -> str:
    processed = job_data["processed_count"]
    total = job_data["total_rows"]
    found = job_data["found_count"]
    start_time = job_data["start_time"]
    c = job_data["counters"]

    pct = round((processed / total) * 100) if total > 0 else 0
    elapsed = max(time.time() - start_time, 0.001)
    rate = processed / elapsed
    eta_sec = round((total - processed) / rate) if rate > 0 else 0
    eta_text = "almost done..." if processed >= total else (f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec > 60 else f"{eta_sec}s")

    total_blocks = 10
    filled_blocks = round((pct / 100) * total_blocks)
    bar = ""
    for i in range(total_blocks):
        if i < filled_blocks:
            bar += "🟩" if i < 4 else ("🟨" if i < 8 else "🟧")
        else:
            bar += "⬜"

    stage_emoji = "🏁" if pct >= 100 else ("🔥" if pct >= 75 else ("⚡" if pct >= 50 else ("🔎" if pct >= 25 else "🚀")))
    success_rate = round((found / max(processed, 1)) * 100)
    success_emoji = "🟢" if success_rate >= 40 else ("🟡" if success_rate >= 10 else "🔴")

    return (
        f"{stage_emoji} *LEAD ENRICHMENT — LIVE STATUS*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{bar}\n"
        f"*{pct}%* Complete\n\n"
        f"📦 *Total Processed:* `{processed:,}` / `{total:,}` rows\n"
        f"⚡ *Realtime Speed:* `{rate:.1f} rows/sec`\n"
        f"⏱ *ETA:* `{eta_text}`\n\n"
        f"📊 *LIVE DISCOVERY STATS:*\n"
        f"📧 *Verified Emails:* `{found:,}` ({success_emoji} `{success_rate}%`)\n"
        f"📞 *Phone Numbers:* `{c['phones']:,}`\n"
        f"📍 *Physical Addresses:* `{c['addresses']:,}`\n\n"
        f"⚠️ *WEBSITE ISSUES BREAKDOWN:*\n"
        f"📝 *Contact Form Only:* `{c['contact_form']:,}`\n"
        f"💀 *Dead / Expired Sites:* `{c['dead_domain']:,}`\n"
        f"🛡️ *Cloudflare / Blocked:* `{c['blocked']:,}`\n"
        f"🚫 *No Website in Row:* `{c['no_url']:,}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 _Click button below to stop anytime & download_"
    )

def build_result_csv(enriched_results: List[Dict[str, str]]) -> bytes:
    if not enriched_results:
        return b""

    PREFERRED_ORDER = [
        'email_found', 'email_source', 'email_status', 'website_issue',
        'all_emails_seen', 'phone_found', 'all_phones_seen',
        'title', 'description', 'site_name', 'keywords', 'language',
        'address_found', 'city', 'state', 'zip', 'location_source',
        'facebook_url', 'instagram_url', 'linkedin_url', 'twitter_url',
        'text_content', 'source_column_used', 'final_url'
    ]
    all_keys = list(enriched_results[0].keys())
    ordered_headers = [k for k in PREFERRED_ORDER if k in all_keys] + [k for k in all_keys if k not in PREFERRED_ORDER if k != '__matched_host']

    out_stream = io.StringIO()
    writer = csv.DictWriter(out_stream, fieldnames=ordered_headers, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(enriched_results)
    return out_stream.getvalue().encode("utf-8")

async def handle_csv_enrichment(bot: TelegramBot, chat_id: int, file_id: str, file_name: str):
    stop_button_markup = {
        "inline_keyboard": [
            [{"text": "🛑 Stop & Download Current Leads", "callback_data": f"stop_{chat_id}"}]
        ]
    }

    status_msg_id = await bot.send_message(chat_id, "🔎 *Got your file — launching diagnostic enrichment engine...*\n\n░░░░░░░░░░░░░░░░░░░░ 0%", reply_markup=stop_button_markup)
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

        has_website_col = any(pick_website_url(r)[0] for r in rows[:15]) or any(
            any(k in col.lower() for k in ['website', 'url', 'domain', 'link', 'href', 'site'])
            for col in rows[0].keys()
        )
        if not has_website_col:
            await bot.edit_message(chat_id, status_msg_id, "❌ *Error:* No website or domain links found in your CSV file!")
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
            "counters": {
                "phones": 0,
                "addresses": 0,
                "new_scraped": 0,
                "input_csv": 0,
                "contact_form": 0,
                "dead_domain": 0,
                "blocked": 0,
                "no_url": 0
            },
            "enriched_results": [],
            "status_msg_id": status_msg_id,
            "queue": queue,
            "workers": []
        }
        ACTIVE_JOBS[chat_id] = job_data

        stop_updater = False

        async def live_progress_loop():
            last_text = ""
            while not stop_updater and not job_data["is_stopped"]:
                await asyncio.sleep(1.8)
                text = render_progress_diagnostics(job_data)
                if text != last_text:
                    await bot.edit_message(chat_id, status_msg_id, text, reply_markup=stop_button_markup)
                    last_text = text

        updater_task = asyncio.create_task(live_progress_loop())

        conn = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT, limit_per_host=4, ssl=False, ttl_dns_cache=600, force_close=False)
        
        async with aiohttp.ClientSession(connector=conn) as scraper_session:
            async def queue_worker():
                while not queue.empty() and not job_data["is_stopped"]:
                    try:
                        item = queue.get_nowait()
                    except (asyncio.QueueEmpty, Exception):
                        break
                    try:
                        res = await process_lead(scraper_session, item, job_data["counters"])
                        if res:
                            if res.get("email_found"):
                                job_data["found_count"] += 1
                                if res.get("email_status") == "VERIFIED_SCRAPED":
                                    job_data["counters"]["new_scraped"] += 1
                                elif res.get("email_status") == "INPUT_CSV":
                                    job_data["counters"]["input_csv"] += 1
                            job_data["enriched_results"].append(res)
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass
                    finally:
                        job_data["processed_count"] += 1
                        queue.task_done()

            workers = [asyncio.create_task(queue_worker()) for _ in range(CONCURRENCY_LIMIT)]
            job_data["workers"] = workers
            await asyncio.gather(*workers, return_exceptions=True)

        stop_updater = True
        updater_task.cancel()

        was_stopped = job_data["is_stopped"]
        ACTIVE_JOBS.pop(chat_id, None)

        final_csv_bytes = build_result_csv(job_data["enriched_results"])
        if not final_csv_bytes:
            await bot.edit_message(chat_id, status_msg_id, "⚠️ Enrichment finished, but no leads could be processed.")
            return

        elapsed = max(time.time() - job_data["start_time"], 0.001)
        elapsed_text = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed > 60 else f"{int(elapsed)}s"
        final_count = len(job_data["enriched_results"])
        avg_speed = f"{(job_data['processed_count'] / elapsed):.1f}"
        c = job_data["counters"]

        status_title = "🛑 *ENRICHMENT STOPPED (EARLY OUTPUT)*" if was_stopped else "✅ *ENRICHMENT COMPLETE!*"
        summary_text = (
            f"{status_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📄 *Total Leads Processed:* `{final_count:,}` rows\n"
            f"📧 *Total Verified Emails:* `{job_data['found_count']:,}`\n"
            f"   ├ ✨ *New Scraped Emails:* `{c['new_scraped']:,}`\n"
            f"   └ 📂 *From Input CSV:* `{c['input_csv']:,}`\n\n"
            f"📞 *Phone Numbers:* `{c['phones']:,}`\n"
            f"📍 *Physical Addresses:* `{c['addresses']:,}`\n\n"
            f"🔍 *WEBSITE DIAGNOSTICS:*\n"
            f"• 📝 Contact Form Only: `{c['contact_form']:,}`\n"
            f"• 💀 Dead / Expired Sites: `{c['dead_domain']:,}`\n"
            f"• 🛡️ Blocked / Cloudflare: `{c['blocked']:,}`\n"
            f"• 🚫 No Website Given: `{c['no_url']:,}`\n\n"
            f"⚡ *Avg Speed:* `{avg_speed} rows/sec` | ⏱ *Total Time:* `{elapsed_text}`\n"
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
    "📧 *Verified Emails* (With exact source links)\n"
    "📞 *Phone Numbers*\n"
    "📍 *Physical Addresses / Location*\n"
    "🌐 *Title, Description & Socials*\n\n"
    "⚡ *Features:*\n"
    "• Up to 50,000 leads supported\n"
    "• Ultra Turbo speed with live diagnostics\n"
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

async def keep_alive_pinger():
    port = int(os.environ.get("PORT", 10000))
    await asyncio.sleep(10)
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                # 1. Local port ping
                try:
                    async with s.get(f"http://127.0.0.1:{port}/", timeout=aiohttp.ClientTimeout(total=4)) as resp1:
                        pass
                except Exception:
                    pass
                # 2. External HTTPS URL ping
                try:
                    async with s.get("https://leads-bot-emzf.onrender.com/", timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                        pass
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(45)

async def main():
    global IS_PUBLIC_ACTIVE
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=500)
    loop.set_default_executor(executor)

    try:
        start_health_thread()
    except Exception as e:
        print(f"Web server notice: {e}")

    asyncio.create_task(keep_alive_pinger())

    bot = TelegramBot(TELEGRAM_BOT_TOKEN)
    await bot.init()
    print("Turbo Lead Enricher Bot is running 24/7 with Live Diagnostics...")
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
                                    j = ACTIVE_JOBS[target_chat_id]
                                    j["is_stopped"] = True
                                    q = j.get("queue")
                                    if q:
                                        while not q.empty():
                                            try:
                                                q.get_nowait()
                                                q.task_done()
                                            except Exception:
                                                break
                                    for w in j.get("workers", []):
                                        if not w.done():
                                            w.cancel()
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

                        # Owner Commands
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

                        # Check Maintenance
                        is_admin = (user_id in ADMIN_USER_IDS)
                        if not IS_PUBLIC_ACTIVE and not is_admin:
                            await bot.send_message(chat_id, MAINTENANCE_MESSAGE)
                            continue

                        # CSV Processing
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
