#!/usr/bin/env python3
"""Visitor stats and errors for a bones site, from nginx logs pulled by ops.sh.

Runs on your machine (standard library only). ops.sh pulls the logs into <stats dir>/raw/ and
calls this; you normally run it as `SHIP_SITE=<site> bash ../bones/ship/ops.sh prod stats` or `... prod errors`.

  stats.py import    <stats dir> --domain D
  stats.py dashboard <stats dir> --site S --domain D [--comments-host H] --build DIR [--other-build DIR ...] [--days N]
  stats.py check     <stats dir> (same options)    how requests were counted: validate the rules
  stats.py errors    <stats dir> (same options)

No personal data is kept: no IP address, no query string, the time only to the hour, only the
referring site's host (the path only for links on the site itself), and a reduced agent: browser
family + major version + mobile/desktop for browsers, the name alone for bots, scripts and feed
readers. Every pulled file is reduced to this on import and rewritten, so older pulls end up
the same. Raw files in <stats dir>/raw/<env>/:
  stats-*.log.gz    nginx "stats" log format, reduced on the server (config/nginx.conf)
  access-*.log.gz   nginx "combined" before the stats log existed: the IP is removed on the
                    server, the rest reduced here on import; no host: the site is told by
                    its pages
  error-*.log.gz    nginx error.log, without client address, referrer and query strings
  monitor-*.log.gz  ship monitor.log
  httpd-*.log.gz    OpenBSD httpd logs (host first, no user agent): page hits, people and bots
                    together; the IP field is removed and the time cut to the hour on import

Each request is stored once: a line seen in several pulled files (rotations, snapshots)
counts as many times as it appears in the one file holding it most often.
When the reductions change (ANON), the next run copies stats.db to stats.db.bak first.
Classification rules live in classify(); change RULES when you change it and the stored rows
are classified again on the next run.
"""
import argparse
import calendar
import gzip
import hashlib
import html
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import unquote, urlsplit

RULES = 3

# -------------------------
# Parsing
# -------------------------
MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}
# OpenBSD httpd: host ip ident user [time] "request" status size
RE_HTTPD = re.compile(r'^(\S+) \S+ \S+ \S+ \[([^\]]+)\] "(.*)" (\d{3}) (\S+)\s*$')
# nginx combined: ip ident user [time] "request" status size "referer" "agent"
RE_COMBINED = re.compile(r'^\S+ \S+ \S+ \[([^\]]+)\] "(.*?)" (\d{3}) (\S+) "(.*?)" "(.*?)"\s*$')
# nginx stats: host [time] "request" status size "referer" "agent" lang fetch
RE_STATS = re.compile(r'^(\S+) \[([^\]]+)\] "(.*?)" (\d{3}) (\S+) "(.*?)" "(.*?)" (\S+) (\S+)\s*$')
# nginx error.log: 2026/11/16 06:02:48 [error] 1#0: *43 message, client: -, server: x, request: "GET / HTTP/1.1", host: "x"
RE_ERROR = re.compile(r'^(\d{4})/(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d) \[(\w+)\] \d+#\d+: (?:\*\d+ )?(.*)$')
# ship monitor.log: 2026-11-16 06:30:01 OK    message
RE_MONITOR = re.compile(r'^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) (\S+)\s+(.*)$')


def parse_time(s):
    """'16/Nov/2026:06:02:48 +0000' -> epoch seconds (UTC). A missing zone is UTC."""
    try:
        d, mon, rest = s[:2], s[3:6], s[7:]
        y, hh, mm, ss = int(rest[0:4]), int(rest[5:7]), int(rest[8:10]), int(rest[11:13])
        t = calendar.timegm((y, MONTHS[mon], int(d), hh, mm, ss))
    except (KeyError, ValueError, IndexError):
        return None
    z = s[20:].strip()
    if len(z) == 5 and z[0] in "+-":
        off = int(z[1:3]) * 3600 + int(z[3:5]) * 60
        t -= off if z[0] == "+" else -off
    return t


def split_request(req):
    parts = req.split(" ")
    if len(parts) >= 3:
        method, target = parts[0], " ".join(parts[1:-1])
    elif len(parts) == 2:
        method, target = parts
    else:
        method, target = (parts[0] if parts else ""), ""
    path, _, query = target.partition("?")
    return method[:16], path[:500], query[:500]


def num(s):
    return int(s) if s.isdigit() else 0


def parse_line(fmt, line):
    """-> dict or None. host is '' when the log does not say."""
    if fmt == "stats":
        m = RE_STATS.match(line)
        if not m:
            return None
        host, t, req, st, size, ref, ua, lang, fetch = m.groups()
    elif fmt == "access":
        m = RE_COMBINED.match(line)
        if not m:
            return None
        t, req, st, size, ref, ua = m.groups()
        host, lang, fetch = "", "-", "-"
    else:  # httpd
        m = RE_HTTPD.match(line)
        if not m:
            return None
        host, t, req, st, size = m.groups()
        ref, ua, lang, fetch = "", "", "-", "-"
    ts = parse_time(t)
    if ts is None:
        return None
    method, path, query = split_request(req)
    return dict(ts=ts, host=host.lower(), method=method, path=path, query=query, status=int(st),
                bytes=num(size), referer=ref[:500], ua=ua[:400], lang=lang.lower()[:2], fetch=fetch[:16])


def file_format(name):
    for p in ("stats", "access", "httpd", "error", "monitor"):
        if name.startswith(p + "-"):
            return p
    return None


def open_text(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def line_id(fmt, line):
    h = hashlib.blake2b(f"{fmt}\n{line}".encode("utf-8", "replace"), digest_size=8).digest()
    return int.from_bytes(h, "big", signed=True)


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# -------------------------
# Classification
# -------------------------
BOT_UA = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|scan|monitor|uptime|check|curl|wget|python|go-http|java/|"
    r"okhttp|axios|node-fetch|undici|ruby|perl|php|libwww|httpclient|http_client|headless|phantom|"
    r"lighthouse|pagespeed|preview|facebookexternalhit|whatsapp|telegram|discord|slack|skype|"
    r"feed|rss|reader|archive|semrush|ahrefs|mj12|dotbot|yandex|baidu|bytespider|petal|"
    r"dataforseo|zgrab|masscan|nmap|nikto|nuclei|censys|httpx|expanse|gpt|claude|anthropic|"
    r"perplexity|cohere|meta-external|google-|bingpreview|mediapartners|validator|wordpress|"
    r"^mozilla/5\.0$|^-?$", re.I)
BROWSER_UA = re.compile(r"^Mozilla/5\.0 \(.+\) .*(Chrome|Safari|Firefox|Edg|OPR)/"
                        r"|^(Edg|OPR|Firefox|FxiOS|CriOS|Chrome|Version)/\d+ [md]$", re.I)
# The same reductions as config/nginx.conf ($stats_agent, $stats_time), applied to every pulled
# line on import: to the combined and httpd logs, and to stats and error lines pulled before a rule changed.
NGINX_BOT = re.compile(r"bot|crawl|spider|slurp|scan|fetch|monitor|uptime|check|preview|feed|rss|"
                       r"reader|archive|python|curl|wget|http|java|headless|lighthouse|semrush|"
                       r"ahrefs|gpt|claude|perplexity", re.I)
NGINX_BOT_NAME = re.compile(r"[A-Za-z0-9_-]*(?:" + NGINX_BOT.pattern + r")[A-Za-z0-9_-]*", re.I)
NGINX_BROWSER = re.compile(r"(Edg|OPR|Firefox|FxiOS|CriOS|Chrome|Version)/(\d+)")
NGINX_MOBILE = re.compile(r"Mobile|Android|iPhone|iPad")
NGINX_SUBS = re.compile(r"(\d+) (?:subscribers?|readers?)", re.I)
NGINX_PRODUCT = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
REDUCED_BROWSER = re.compile(r"^((Edg|OPR|Firefox|FxiOS|CriOS|Chrome|Version)/\d+)? [md]$")
ANON = 3  # version of these reductions: a change makes the next run read every raw file again


def anon_agent(ua):
    """Browser: family/major + m or d. Anything else: its name, and a feed service's subscriber count."""
    if REDUCED_BROWSER.match(ua):
        return ua
    m = NGINX_SUBS.search(ua)
    subs = f" {m.group(1)} subscribers" if m else ""
    if ua.startswith("Mozilla/"):
        if NGINX_BOT.search(ua):
            return NGINX_BOT_NAME.search(ua).group(0) + subs
        if ua.startswith("Mozilla/5.0 ("):
            m = NGINX_BROWSER.search(ua)
            return (f"{m.group(1)}/{m.group(2)}" if m else "") + (" m" if NGINX_MOBILE.search(ua) else " d")
    m = NGINX_PRODUCT.match(ua)
    return (m.group(0) if m else "-") + subs


def anon_time(t):
    """'16/Nov/2026:06:02:48 +0000' -> '16/Nov/2026:06:00:00 +0000'"""
    return re.sub(r"^([^:]+:\d\d):\d\d:\d\d", r"\1:00:00", t)


def anon_referer(ref, own_hosts):
    m = re.match(r"^https?://([^/:?#]+)[^/?#]*(/[^?#]*)?", ref)
    if not m:
        return ref or "-"  # "-" or already reduced
    host = m.group(1).lower()
    return host + (m.group(2) or "") if host in own_hosts else host


def anon_access(counts, own_hosts):
    """The combined log reduced like the stats log."""
    out = Counter()
    for line, n in counts.items():
        m = RE_COMBINED.match(line)
        if not m:
            continue
        t, req, st, size, ref, ua = m.groups()
        method, path, _ = split_request(req)
        out[f'- - - [{anon_time(t)}] "{method} {path}" {st} {size} "{anon_referer(ref, own_hosts)}" "{anon_agent(ua)}"'] += n
    return out


def anon_stats(counts):
    """Stats lines pulled before the server reduced the agent and the time."""
    out = Counter()
    for line, n in counts.items():
        m = RE_STATS.match(line)
        if not m:
            continue
        host, t, req, st, size, ref, ua, lang, fetch = m.groups()
        out[f'{host} [{anon_time(t)}] "{req.partition("?")[0]}" {st} {size} "{ref}" "{anon_agent(ua)}" {lang} {fetch}'] += n
    return out


def anon_httpd(counts):
    """httpd lines with the time only to the hour: requests that become the same line add up."""
    out = Counter()
    for line, n in counts.items():
        m = RE_HTTPD.match(line)
        if not m:
            continue
        host, t, req, st, size = m.groups()
        out[f'{host} - - - [{anon_time(t)}] "{req}" {st} {size}'] += n
    return out


def anon_error(counts):
    """error.log lines without client address, referrer and query strings."""
    out = Counter()
    for line, n in counts.items():
        line = re.sub(r", client: [^,]+", ", client: -", line)
        line = re.sub(r', referrer: "[^"]*"', "", line)
        line = re.sub(r'(request: "[A-Z]+ [^ ?"]*)\?[^ "]*', r"\1", line)
        line = re.sub(r'(upstream: "[^?"]*)\?[^"]*', r"\1", line)
        out[line] += n
    return out


CHROME_V = re.compile(r"(?:Chrome|Firefox|Edg|CriOS|FxiOS)/(\d+)\b")
SAFARI_V = re.compile(r"^Version/(\d+) [md]$")
IOS_V = re.compile(r"iPhone OS (\d+)_")
CHROME_120 = 1701734400  # 2023-12-05: Chrome 120; Chrome, Edge and Firefox ship a major every 4 weeks
STALE_RELEASES = 10      # browsers update themselves: ~9 months behind is a bot with a fixed agent


def stale_browser(ua, ts):
    """Scrapers keep one old agent string forever (e.g. Chrome/120.0.0.0 in 2026)."""
    m = CHROME_V.search(ua)
    if m and int(m.group(1)) < 120 + (ts - CHROME_120) // (28 * 86400) - STALE_RELEASES:
        return True
    m = IOS_V.search(ua) or SAFARI_V.match(ua)
    return bool(m and int(m.group(1)) < 15)  # Safari/iOS 15 is from 2021; the iOS in agents stops at 18_x


PROBE_PATH = re.compile(
    r"\.(php\d?|env|git|svn|aws|asp|aspx|jsp|jspa|cgi|pl|sql|bak|old|orig|swp|ini|ya?ml|log|"
    r"conf|cfg|config|properties|toml|lock|key|pem|crt|p12|sqlite|db|sh|bash|py|rb|exe|dll|"
    r"DS_Store|htaccess|htpasswd)(/|$)|/\.|\.\.|phpinfo|server-(info|status)|_profiler|"
    r"/(env|credentials|secrets?|appsettings|settings|auth|composer|package)([._-][\w.-]*)?$|"
    r"/wp-(admin|login|includes|json|config|cron|signup|trackback)|/wp-content/(plugins|themes)|"
    r"/(wordpress|wp|cgi-bin|xmlrpc|phpmyadmin|pma|vendor|node_modules|boaform|actuator|hnap1|owa|"
    r"remote|console|solr|geoserver|manager|jenkins|druid|telescope|debug|admin|administrator|"
    r"login|user|api|v\d|backup|config|setup|install|shell|cmd|cgi)(/|$)", re.I)
PROBE_QUERY = re.compile(r"rest_route|phpinfo|xdebug|cmd=|exec|wget|curl|<script|union|select|base64|"
                         r"\.\./|/etc/|allow_url|auto_prepend|eval\(|%00", re.I)
ASSET = re.compile(r"\.(css|js|mjs|map|jpe?g|png|gif|webp|avif|svg|ico|woff2?|ttf|otf|eot|"
                   r"mp4|webm|mp3|pdf|txt|xml|json|webmanifest)$|^/assets/", re.I)
FEED = re.compile(r"^/(feed\.xml|feed/?|rss/?.*|atom\.xml|index\.xml)$", re.I)

KINDS = {
    "read": "page read by a person (browser, Sec-Fetch-Dest: document)",
    "read?": "page read, likely a person (browser agent, no Sec-Fetch-Dest)",
    "hit": "page request in httpd logs (no agent: people and bots together)",
    "bot": "page or file request by a bot, monitor or script",
    "feed": "feed fetch",
    "download": "file download from /pub/",
    "comment": "comment posted",
    "comments": "other comment server traffic",
    "asset": "image, script, font, robots.txt, sitemap ...",
    "redirect": "301/302/307/308",
    "notfound": "404 not found",
    "probe": "vulnerability scan or bad request (400/403/405/429/444)",
    "error": "5xx server error",
    "other": "anything else (HEAD, odd queries, other hosts ...)",
}
PEOPLE = ("read", "read?", "hit")


def is_bot(r):
    """A bot's agent: named as one, not a browser's, or a browser version long out of date."""
    ua = r["ua"]
    return r["src"] != "httpd" and bool(BOT_UA.search(ua) or not BROWSER_UA.search(ua) or stale_browser(ua, r["ts"]))


def classify(r, comments_host):
    method, path, query, st, ua = r["method"], r["path"], r["query"], r["status"], r["ua"]
    if comments_host and r["host"] == comments_host:
        if method == "POST" and path == "/new" and st in (200, 201):
            return "comment"
        return "error" if st >= 500 else "comments"
    if st >= 500:
        return "error"
    if PROBE_PATH.search(path) or (query and PROBE_QUERY.search(unquote(query))) \
            or st in (400, 403, 405, 429, 444) or method not in ("GET", "HEAD", "POST", "OPTIONS"):
        return "probe"
    no_agent = r["src"] == "httpd"
    bot = is_bot(r)
    if st in (301, 302, 307, 308):
        return "redirect"
    if st == 404:
        return "notfound"
    if st >= 400:
        return "probe"
    if method != "GET":
        return "other"
    if FEED.match(path):
        return "feed"
    if path.startswith("/pub/") and not path.endswith("/"):
        return "bot" if bot else "download"
    if ASSET.search(path):
        return "asset"
    if st not in (200, 304) or not (path.endswith("/") or path.endswith(".html")):
        return "other"
    if no_agent:
        return "hit"
    if bot:
        return "bot"
    if r["fetch"] == "document":
        return "read"
    if r["fetch"] in ("-", ""):
        return "read?"
    return "other"  # a browser fetching a page as iframe, prefetch, script ...


# -------------------------
# Store
# -------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS hits(id INTEGER PRIMARY KEY, n INTEGER, ts INTEGER, day TEXT, src TEXT,
  host TEXT, method TEXT, path TEXT, query TEXT, status INTEGER, bytes INTEGER, referer TEXT,
  ua TEXT, lang TEXT, fetch TEXT, kind TEXT);
DROP INDEX IF EXISTS hits_day;
-- Covers every report query that only sums counts: no row of the (large) table is read for them.
CREATE INDEX IF NOT EXISTS hits_cover ON hits(day, kind, host, path, n, src, ts);
CREATE TABLE IF NOT EXISTS errlog(id INTEGER PRIMARY KEY, n INTEGER, ts INTEGER, day TEXT,
  level TEXT, msg TEXT, host TEXT, request TEXT);
CREATE INDEX IF NOT EXISTS errlog_day ON errlog(day);
CREATE TABLE IF NOT EXISTS monitor(id INTEGER PRIMARY KEY, n INTEGER, ts INTEGER, day TEXT,
  level TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY, size INTEGER, mtime REAL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(stats_dir):
    os.makedirs(stats_dir, exist_ok=True)
    db = sqlite3.connect(os.path.join(stats_dir, "stats.db"))
    db.executescript(SCHEMA)
    return db


def meta(db, k, v=None):
    if v is None:
        row = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, str(v)))


def raw_files(stats_dir):
    root = os.path.join(stats_dir, "raw")
    for d, _, names in os.walk(root):
        for name in sorted(names):
            fmt = file_format(name)
            if fmt and (name.endswith(".log.gz") or name.endswith(".log")):
                yield os.path.join(d, name), os.path.relpath(os.path.join(d, name), root), fmt


def import_file(db, path, fmt, comments_host, own_hosts):
    counts = Counter()
    bad = 0
    with open_text(path) as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                counts[line] += 1
    if fmt in ("access", "stats", "httpd", "error"):
        anon = {"access": lambda: anon_access(counts, own_hosts), "stats": lambda: anon_stats(counts),
                "httpd": lambda: anon_httpd(counts), "error": lambda: anon_error(counts)}[fmt]()
        if anon != counts:  # keep only the reduced lines on disk too
            tmp = path + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                for line, n in anon.items():
                    f.write((line + "\n") * n)
            os.replace(tmp, path)
        counts = anon
    rows = []
    if fmt in ("stats", "access", "httpd"):
        for line, n in counts.items():
            r = parse_line(fmt, line)
            if r is None:
                bad += n  # newsyslog markers, garbage
                continue
            r["src"] = fmt
            kind = classify(r, comments_host)
            # Only httpd logs still carry a query: kept for scans (to see them), dropped for the rest.
            query = r["query"] if kind == "probe" else ("?" if r["query"] else "")
            rows.append((line_id(fmt, line), n, r["ts"], day_of(r["ts"]), fmt, r["host"], r["method"],
                         r["path"], query, r["status"], r["bytes"], r["referer"], r["ua"],
                         r["lang"], r["fetch"], kind))
        db.executemany("INSERT INTO hits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET n=max(n, excluded.n)", rows)
    elif fmt == "error":
        for line, n in counts.items():
            m = RE_ERROR.match(line)
            if not m:
                bad += n
                continue
            y, mo, d, hh, mi, ss, level, msg = m.groups()
            ts = calendar.timegm((int(y), int(mo), int(d), int(hh), int(mi), int(ss)))
            host = re.search(r', host: "([^"]*)"', msg)
            req = re.search(r', request: "([^"]*)"', msg)
            text = re.sub(r", (client|server|request|host|upstream|referrer): .*$", "", msg)
            rows.append((line_id(fmt, line), n, ts, day_of(ts), level, text[:300],
                         host.group(1).lower() if host else "", req.group(1)[:300] if req else ""))
        db.executemany("INSERT INTO errlog VALUES(?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET n=max(n, excluded.n)", rows)
    elif fmt == "monitor":
        for line, n in counts.items():
            m = RE_MONITOR.match(line)
            if not m:
                continue
            y, mo, d, hh, mi, ss, level, msg = m.groups()
            # monitor.log is written in the server's local time; the server runs on UTC
            ts = calendar.timegm((int(y), int(mo), int(d), int(hh), int(mi), int(ss)))
            rows.append((line_id(fmt, line), n, ts, day_of(ts), level, msg[:300]))
        db.executemany("INSERT INTO monitor VALUES(?,?,?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET n=max(n, excluded.n)", rows)
    return len(rows), bad


def run_import(stats_dir, comments_host, domain, quiet=False):
    own_hosts = {domain, "www." + domain} if domain else set()
    db = connect(stats_dir)
    # All sites share this database and the server has one comments host (sites.conf allows one).
    # A site without comments gives none: keep the stored one, or every run for the other site
    # would classify all rows again (minutes on a few million rows).
    comments_host = comments_host or meta(db, "comments_host") or ""
    read_again = meta(db, "anon") != str(ANON)
    if read_again:  # the reductions changed: read and rewrite every raw file again
        if db.execute("SELECT 1 FROM files LIMIT 1").fetchone():
            bak = os.path.join(stats_dir, "stats.db.bak")
            print(f"Reading all log files again once (the rules for what is kept changed). This can take minutes."
                  f" The database before it: {bak}", flush=True)
            copy = sqlite3.connect(bak)
            db.backup(copy)
            copy.close()
        db.executescript("DELETE FROM hits; DELETE FROM errlog; DELETE FROM files;")
        meta(db, "anon", ANON)
        db.commit()
    known = {name: (size, mtime) for name, size, mtime in db.execute("SELECT name, size, mtime FROM files")}
    done = 0
    for path, rel, fmt in raw_files(stats_dir):
        st = os.stat(path)
        if known.get(rel) == (st.st_size, st.st_mtime):
            continue
        rows, bad = import_file(db, path, fmt, comments_host, own_hosts)
        st = os.stat(path)  # the file may have been rewritten in its reduced form
        db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (rel, st.st_size, st.st_mtime))
        db.commit()
        done += 1
        if not quiet:
            print(f"  imported {rel}: {rows} distinct lines" + (f", {bad} not parsed" if bad else ""))
    # Lines of the combined log are only needed before the stats log existed: after that the same
    # requests are in both.
    first_stats = db.execute("SELECT min(ts) FROM hits WHERE src='stats'").fetchone()[0]
    if first_stats is not None:
        db.execute("DELETE FROM hits WHERE src='access' AND ts >= ?", (first_stats,))
    if meta(db, "rules") != str(RULES) or meta(db, "comments_host") != (comments_host or ""):
        reclassify(db, comments_host)
        meta(db, "rules", RULES)
        meta(db, "comments_host", comments_host or "")
    db.commit()
    if read_again:
        db.execute("VACUUM")  # the rows deleted above leave the file at its old size
    return db, done


def reclassify(db, comments_host):
    cols = "id, ts, src, host, method, path, query, status, ua, fetch, kind"
    updates = []
    for row in db.execute(f"SELECT {cols} FROM hits"):
        r = dict(zip(cols.split(", "), row))
        kind = classify(r, comments_host)
        if kind != r["kind"]:  # writing every row rewrites the index: minutes instead of seconds
            updates.append((kind, r["id"]))
    db.executemany("UPDATE hits SET kind=? WHERE id=?", updates)


# -------------------------
# Site selection and pages
# -------------------------
class Site:
    def __init__(self, a):
        self.name, self.domain = a.site, a.domain.lower()
        self.comments_host = (a.comments_host or "").lower()
        self.hosts = [h for h in (self.domain, "www." + self.domain, self.comments_host) if h]
        self.pages = pages_of(a.build)
        self.other_pages = set()
        for b in a.other_build or []:
            self.other_pages |= set(pages_of(b))
        self.build = a.build

    def where(self, alias=""):
        """SQL for rows of this site. Rows without a host (combined log of the shared server) count
        when the path is one of this site's pages and not another site's."""
        p = alias
        own = sorted(set(self.pages) - self.other_pages)
        hosts = ",".join("'" + h.replace("'", "") + "'" for h in self.hosts)
        sql = f"({p}host IN ({hosts})"
        if own:
            sql += f" OR ({p}host='' AND {p}path IN (SELECT path FROM own_pages))"
        return sql + ")", own


def pages_of(build):
    pages = {}
    if not build or not os.path.isdir(build):
        return pages
    for d, _, names in os.walk(build):
        for name in names:
            if not name.endswith(".html") or name == "404.html":
                continue
            rel = os.path.relpath(os.path.join(d, name), build).replace(os.sep, "/")
            url = "/" + (rel[: -len("index.html")] if rel.endswith("index.html") else rel)
            if not ASSET.search(url):
                pages[url] = os.path.join(d, name)
    return pages


def title_of(site, path):
    f = site.pages.get(path) or site.pages.get(unquote(path))
    if not f:
        return ""
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            m = re.search(r"<title>(.*?)</title>", fh.read(20000), re.S | re.I)
    except OSError:
        return ""
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


# -------------------------
# Output
# -------------------------
TTY = sys.stdout.isatty()
if os.name == "nt":
    os.system("")  # turns on ANSI colours in the Windows console
WIDTH = max(80, min(shutil.get_terminal_size((100, 30)).columns, 130))


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if TTY or os.environ.get("FORCE_COLOR") else s


def bold(s): return c("1", s)
def dim(s): return c("2", s)
def green(s): return c("32", s)
def red(s): return c("31", s)
def yellow(s): return c("33", s)
def cyan(s): return c("36", s)


def h1(s, start=None, end=None):
    """Section title; with start and end it shows the date range."""
    print()
    print(bold(cyan(s)) + (dim(f"   {span(start, end)}") if start else ""))
    print(dim("─" * min(WIDTH, 100)))


def span(start, end):
    return start if start == end else f"{start} → {end}"


# Bars: a thin line in a muted colour, so rows stay apart. One look for every chart.
BAR = "38;5;67"       # muted steel blue
BAR_REST = "38;5;240"  # grey: the part not counted as people (bots; all of it in httpd logs)


def bar(v, vmax, width=30):
    """Length of the bar in cells (halves rounded up)."""
    if vmax <= 0 or v <= 0:
        return 0
    return max(1, round(v / vmax * width))


def draw_bar(main, rest, vmax, width):
    """main as a line in BAR, then rest (counted on top of main) in grey; padded to width."""
    a = bar(main, vmax, width)
    b = max(0, bar(main + rest, vmax, width) - a)
    return c(BAR, "━" * a) + c(BAR_REST, "─" * b) + " " * (width - a - b)


def fmt_n(n):
    return f"{n:,}".replace(",", " ")


def delta(now, before):
    if not before:
        return dim("   new") if now else dim("     -")
    p = (now - before) / before * 100
    s = f"{p:+5.0f}%"
    return green(s) if p >= 0 else red(s)


def cut(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def table(rows, vmax=None, bar_w=24, label_w=None):
    """rows: (count, label, extra). Prints count, bar, label."""
    if not rows:
        print(dim("  (none)"))
        return
    vmax = vmax or max(r[0] for r in rows)
    label_w = label_w or WIDTH - bar_w - 14
    for r in rows:
        extra = str(r[2]) if len(r) > 2 and r[2] else ""
        label = cut(r[1], label_w - (len(extra) + 2 if extra else 0))
        print(f"  {fmt_n(r[0]):>8} {pad_bar(r[0], vmax, bar_w)} {label}" + (f"  {dim(extra)}" if extra else ""))


def pad_bar(v, vmax, w):
    return draw_bar(v, 0, vmax, w)


# -------------------------
# Reports
# -------------------------
class Q:
    """Queries over one site's rows."""

    def __init__(self, db, site):
        self.db, self.site = db, site
        self.where, own = site.where()
        db.execute("CREATE TEMP TABLE IF NOT EXISTS own_pages(path TEXT PRIMARY KEY)")
        db.execute("DELETE FROM own_pages")
        db.executemany("INSERT OR IGNORE INTO own_pages VALUES(?)", [(p,) for p in own])

    def rows(self, sql, args=()):
        return self.db.execute(sql.replace("{site}", self.where), args).fetchall()

    def one(self, sql, args=()):
        return self.rows(sql, args)[0][0] or 0


def days_back(n, end=None):
    end = end or time.time()
    return day_of(end - (n - 1) * 86400), day_of(end)


def dashboard(db, site, days):
    q = Q(db, site)
    today = day_of(time.time())
    first, last_ts = q.rows("SELECT min(day), max(ts) FROM hits WHERE {site}")[0]
    if not first:
        print(red("No data for this site yet. Pull logs first: SHIP_SITE=<site> bash ../bones/ship/ops.sh prod stats"))
        return
    start, end = days_back(days)
    pstart, pend = days_back(days, time.time() - days * 86400)
    age_h = (time.time() - last_ts) / 3600
    print(bold(f"{site.name} ({site.domain}) — visitor stats") + dim(f"   {time.strftime('%Y-%m-%d %H:%M')}"))
    print(dim("Counts without personal data: no IP addresses, query strings or full agents are kept, and the"
              " time only to the hour."))
    print(dim(f"Data {first} → {day_of(last_ts)} {time.strftime('%H:%M', time.localtime(last_ts))}"
              f" (newest request {age_h:.1f} h ago). People = reads by browsers; months from httpd logs:"
              f" page hits incl. bots."))

    # Summary
    h1("Readers")
    people = "kind IN ('read','read?','hit')"

    def people_between(a, b):
        return q.one(f"SELECT sum(n) FROM hits WHERE {{site}} AND {people} AND day BETWEEN ? AND ?", (a, b))

    for label, n in (("Today", 1), ("7 days", 7), (f"{days} days", days), ("365 days", 365)):
        a, b = days_back(n)
        pa, pb = days_back(n, time.time() - n * 86400)
        now, before = people_between(a, b), people_between(pa, pb)
        if n == 1:  # today so far against yesterday until the same time
            midnight = time.mktime(time.strptime(a, "%Y-%m-%d"))
            t0, t1 = midnight - 86400, time.time() - 86400
            before = q.one(f"SELECT sum(n) FROM hits WHERE {{site}} AND {people} AND ts BETWEEN ? AND ?"
                           " AND day BETWEEN ? AND ?", (t0, t1, day_of(t0), day_of(t1)))  # day: lets the index narrow it
        conf = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='read' AND day BETWEEN ? AND ?", (a, b))
        print(f"  {label:<10} {bold(f'{fmt_n(now):>8}')} {delta(now, before)} vs previous"
              + dim(f"   {span(a, b):<25}") + (dim(f"{fmt_n(conf)} confirmed by Sec-Fetch") if conf else ""))
    dl = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='download' AND day BETWEEN ? AND ?", (start, end))
    cm = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='comment' AND day BETWEEN ? AND ?", (start, end))
    print(f"  {days} days: {bold(fmt_n(dl))} downloads, {bold(fmt_n(cm))} comments posted" + dim(f"   {span(start, end)}"))

    # Daily
    a, b = days_back(14)
    h1("Last 14 days, people per day", a, b)
    daily = dict(q.rows(f"SELECT day, sum(n) FROM hits WHERE {{site}} AND {people} AND day >= ? GROUP BY day", (a,)))
    dmax = max(daily.values(), default=0)
    for i in range(13, -1, -1):
        d = day_of(time.time() - i * 86400)
        wd = time.strftime("%a", time.strptime(d, "%Y-%m-%d"))
        v = daily.get(d, 0)
        print(f"  {d} {wd} {fmt_n(v):>7} {pad_bar(v, dmax, 50)}" + (dim(" (so far)") if d == today else ""))

    # Monthly
    h1("By month")
    rows = q.rows(f"""SELECT substr(day,1,7) m,
        sum(CASE WHEN kind IN ('read','read?') THEN n ELSE 0 END),
        sum(CASE WHEN kind IN ('read','read?','hit','bot') THEN n ELSE 0 END),
        count(DISTINCT day) FROM hits WHERE {{site}} GROUP BY m ORDER BY m""")
    mmax = max((r[2] for r in rows), default=0)
    print(dim(f"  {'month':<8} {'people':>8} {'page hits':>10}  days  ") + c(BAR, "━━ people") + "  "
          + c(BAR_REST, "── other page hits (bots; httpd logs cannot tell them apart)"))
    have = {r[0]: r for r in rows}
    # every month from this one back to the first, so gaps show
    y, mo = map(int, today[:7].split("-"))
    fy, fm = map(int, rows[0][0].split("-"))
    while (y, mo) >= (fy, fm):
        m = f"{y:04d}-{mo:02d}"
        dim_days = calendar.monthrange(y, mo)[1] if m != today[:7] else int(today[8:])
        if m in have:
            _, ppl, hits, nd = have[m]
            graph = draw_bar(ppl, hits - ppl, mmax, 40)
            cov = f"{nd:>2}/{dim_days:<2}"
            cov = cov if nd >= dim_days else yellow(cov)
            ppl_s = f"{fmt_n(ppl) if ppl else '-':>8}"
            print(f"  {m:<8} {ppl_s if ppl else dim(ppl_s)} {fmt_n(hits):>10}  {cov} {graph}")
        else:
            print(f"  {m:<8} {dim(f'{chr(45):>8} {chr(45):>10}  no data')}")
        mo -= 1
        if mo < 1:
            y, mo = y - 1, 12

    # Top pages
    h1(f"Top pages, {days} days (people)", start, end)
    hits = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='hit' AND day BETWEEN ? AND ?", (start, end))
    if hits:
        print(dim(f"  includes {fmt_n(hits)} page hits from httpd logs: people and bots together"))
    top = q.rows(f"""SELECT path, sum(n) s, sum(CASE WHEN kind='read' THEN n ELSE 0 END) FROM hits
        WHERE {{site}} AND {people} AND day BETWEEN ? AND ? GROUP BY path ORDER BY s DESC LIMIT 20""", (start, end))
    table([(s, p, title_of(site, p)) for p, s, _ in top])

    # Referrers
    h1(f"Where people came from, {days} days", start, end)
    refs = Counter()
    for ref, n in q.rows(f"""SELECT referer, sum(n) FROM hits WHERE {{site}} AND kind IN ('read','read?')
            AND day BETWEEN ? AND ? GROUP BY referer""", (start, end)):
        refs[ref_name(ref, site)] += n
    total = sum(refs.values())
    table([(n, name, f"{n / total * 100:.0f}%") for name, n in refs.most_common(15)])

    # Downloads, feeds, languages
    h1(f"Downloads, {days} days", start, end)
    table(q.rows("""SELECT sum(n) s, path FROM hits WHERE {site} AND kind='download' AND day BETWEEN ? AND ?
        GROUP BY path ORDER BY s DESC LIMIT 10""", (start, end)))

    h1(f"Feed, {days} days", start, end)
    feed_report(q, start, end)

    h1(f"Readers' language, {days} days", start, end)
    langs = q.rows("""SELECT lang, sum(n) s FROM hits WHERE {site} AND kind IN ('read','read?') AND lang != '-'
        AND day BETWEEN ? AND ? GROUP BY lang ORDER BY s DESC LIMIT 6""", (start, end))
    lt = sum(r[1] for r in langs)
    print("  " + ("   ".join(f"{l} {n / lt * 100:.0f}%" for l, n in langs) if langs
                  else dim("(only in the stats log: from the nginx stats log onwards)")))

    # Mix
    h1(f"All requests, {days} days", start, end)
    mix = q.rows("""SELECT kind, sum(n) s FROM hits WHERE {site} AND day BETWEEN ? AND ?
        GROUP BY kind ORDER BY s DESC""", (start, end))
    mt = sum(r[1] for r in mix)
    for k, n in mix:
        print(f"  {k:<9} {fmt_n(n):>9} {n / mt * 100:5.1f}%  {dim(KINDS.get(k, ''))}")

    # Errors
    h1(f"Errors, {days} days", start, end)
    error_summary(db, q, site, start, end)

    # Validation
    h1("Counting")
    ppl, allpages = q.rows(f"""SELECT sum(CASE WHEN kind IN ('read','read?') THEN n ELSE 0 END),
        sum(CASE WHEN kind IN ('read','read?','bot') THEN n ELSE 0 END) FROM hits
        WHERE {{site}} AND src IN ('stats','access') AND day BETWEEN ? AND ?""", (start, end))[0]
    if allpages:
        print(f"  In new logs {(allpages - (ppl or 0)) / allpages * 100:.0f}% of page hits came from bots."
              f" Months from httpd logs count page hits: people and bots together.")
    print(dim(f"  Details and the rules: SHIP_SITE={site.name} bash ../bones/ship/ops.sh prod stats --check   Errors: ... prod errors"))


SEARCH = re.compile(r"(^|\.)(google|bing|duckduckgo|yahoo|ecosia|qwant|brave|yandex|baidu|startpage)\.", re.I)


def ref_name(ref, site):
    if not ref or ref == "-":
        return "(direct, apps, bookmarks)"
    host = (urlsplit(ref if "://" in ref else "//" + ref).hostname or ref[:40]).lower()
    if host.startswith("www."):
        host = host[4:]
    if host == site.domain or host.endswith("." + site.domain):
        return "(own pages)"
    m = SEARCH.search(host)
    if m:
        return m.group(2) + " (search)"
    if host in ("t.co", "x.com", "twitter.com"):
        return "x.com"
    if host.endswith("facebook.com"):
        return "facebook.com"
    return host


def feed_report(q, start, end):
    rows = q.rows("""SELECT ua, sum(n) FROM hits WHERE {site} AND kind='feed' AND day BETWEEN ? AND ?
        GROUP BY ua""", (start, end))
    fetches = sum(n for _, n in rows)
    subs = {}
    for ua, _ in rows:
        m = re.search(r"(\d+) (subscribers?|readers?)\b", ua, re.I)
        if m:
            name = re.split(r"[ /;(]", ua.strip())[0] or ua[:20]
            subs[name] = max(subs.get(name, 0), int(m.group(1)))
    agents = len([u for u, _ in rows if u])
    print(f"  {fmt_n(fetches)} fetches by {agents} different readers/agents"
          + (f"; services report {bold(fmt_n(sum(subs.values())))} subscribers: "
             + ", ".join(f"{k} {v}" for k, v in sorted(subs.items(), key=lambda x: -x[1])) if subs else ""))


def error_summary(db, q, site, start, end):
    """Counts, then the to-do list (what to fix, what fixed itself)."""
    e5 = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='error' AND day BETWEEN ? AND ?", (start, end))
    nf = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='notfound' AND day BETWEEN ? AND ?", (start, end))
    probes = q.one("SELECT sum(n) FROM hits WHERE {site} AND kind='probe' AND day BETWEEN ? AND ?", (start, end))
    print(dim(f"  {fmt_n(e5)} server errors (5xx), {fmt_n(nf)} not found (404), "
              f"{fmt_n(probes)} scans and bad requests blocked"))
    items = findings(db, q, site, start, end)
    todo = [i for i in items if i[0] == "fix"]
    gone = [i for i in items if i[0] == "gone"]
    if not todo:
        print(green("  Nothing to do."))
    for _, what, action, last in todo:
        print(f"  {red('●')} {what}  {dim('last ' + when(last))}")
        print(f"    {dim('→')} {action}")
    if gone:
        print(dim(f"  Stopped by itself or already fixed (not seen in the last {QUIET_DAYS} days):"))
        for _, what, _, last in gone:
            print(dim(f"  ✓ {cut(what, WIDTH - 30)}  last {when(last)}"))


QUIET_DAYS = 2  # an error not seen for this long is shown as fixed, not as to do


def when(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def broken_links(q, site, start, end):
    """404s that a person's browser asked for from one of the site's pages: [(n, path, page, last)].
    Scanners name the site as the referrer too, often the very address they ask for. So the
    referrer must be a page in the build, not the request itself, and the agent a current browser."""
    found = {}
    for path, ref, ua, src, ts, n in q.rows(f"""SELECT path, referer, ua, src, ts, n FROM hits WHERE {{site}}
            AND kind='notfound' AND day BETWEEN ? AND ?
            AND (referer LIKE '{site.domain}/%' OR referer LIKE 'https://{site.domain}/%')""", (start, end)):
        page = urlsplit(ref if "://" in ref else "//" + ref).path
        if page == path or not (page in site.pages or unquote(page) in site.pages):
            continue
        if is_bot({"ua": ua, "src": src, "ts": ts}):
            continue
        count, last = found.get((path, page), (0, 0))
        found[(path, page)] = (count + n, max(last, ts))
    return sorted(((n, path, page, last) for (path, page), (n, last) in found.items()), reverse=True)


def findings(db, q, site, start, end):
    """(state, what, action, last seen): state 'fix' or 'gone' (not seen for QUIET_DAYS)."""
    out = []
    quiet = time.time() - QUIET_DAYS * 86400
    extra = f"ship/{site.name}.nginx.extra in the sites repo"

    def add(what, action, last):
        out.append(("fix" if last >= quiet else "gone", what, action, last))

    for path, n, last in q.rows("""SELECT host || path, sum(n), max(ts) FROM hits WHERE {site} AND kind='error'
            AND day BETWEEN ? AND ? GROUP BY host, path""", (start, end)):
        add(f"{n}× server error on {path}",
            f"See the nginx error.log section of `SHIP_SITE={site.name} bash ../bones/ship/ops.sh prod errors` at that time", last)
    for n, path, page, last in broken_links(q, site, start, end):
        add(f"broken link to {path} on {page} ({n}×)", f"Fix the link in the post for {page}; bash ../bones/ship/deploy-site.sh prod {site.name}", last)
    # files and pages that people keep asking for: an old link somewhere outside the site
    for path, n, last, refs in q.rows(f"""SELECT path, sum(n), max(ts), group_concat(DISTINCT referer) FROM hits
            WHERE {{site}} AND kind='notfound' AND day BETWEEN ? AND ?
            AND referer NOT LIKE '%{site.domain}/%' GROUP BY path
            HAVING sum(n) >= 3 OR (path LIKE '/pub/%' AND path NOT LIKE '%/')""",
                                      (start, end)):
        named = ref_list(refs, site)
        if path.startswith("/pub/") and not path.endswith("/"):
            add(f"missing download {path} ({n}×)",
                f"Add the file to {site.name}/pub/, or redirect it to the right file in {extra}:"
                f" location = {path} {{ return 301 /pub/<file>; }}  then bash ../bones/ship/deploy-server.sh prod", last)
        elif named and (path.endswith("/") or "." not in path.rsplit("/", 1)[-1]):
            add(f"old address {path} ({n}×, linked from {named})",
                f"Redirect it in {extra}: location = {path} {{ return 301 /<new>/; }}  then bash ../bones/ship/deploy-server.sh prod",
                last)
    for (lvl, msg), (n, first, last) in errlog_groups(db, site, start, end).items():
        if noise(msg):
            continue
        add(f"nginx {lvl}: {cut(msg, 70)} ({n}×)", nginx_advice(msg), last)
    runs = db.execute("SELECT max(ts) FROM monitor WHERE level='----'").fetchone()[0] or 0
    for lvl, msg, n, last in db.execute("""SELECT level, msg, sum(n), max(ts) FROM monitor WHERE day BETWEEN ? AND ?
            AND level IN ('ERROR','WARN','FAIL') GROUP BY level, msg""", (start, end)):
        # the monitor repeats a problem every run: gone when the latest run does not report it
        state = "fix" if last >= runs else "gone"
        out.append((state, f"monitor {lvl}: {msg} ({n}×)",
                    "Run bash ../bones/ship/ops.sh prod status to see it now; the monitor mails while it fails", last))
    return sorted(out, key=lambda i: -i[3])


def nginx_advice(msg):
    if "Too many open files" in msg:
        return "nginx ran out of file descriptors: raise worker_rlimit_nofile in bones/ship/config/nginx.conf"
    if "upstream" in msg or "Connection refused" in msg:
        return "isso did not answer: bash ../bones/ship/ops.sh prod check, then ... prod restart isso"
    if "No space left" in msg:
        return "Disk full: bash ../bones/ship/ops.sh prod status"
    if "permission denied" in msg.lower():
        return "File permissions on the server: check the path in the message"
    return "Look up this nginx message; bash ../bones/ship/ops.sh prod logs nginx shows the lines"


# nginx error.log lines that are not something to fix: scans, rate limits, clients that went away,
# and a file that is not there (that is a 404, counted in the hits; a missing download in /pub/ is
# found there too).
NOISE = re.compile(r"access forbidden by rule|limiting requests|client sent invalid|client intended to send "
                   r"too large|SSL_do_handshake\(\) failed|no \"ssl_certificate\"|"
                   r"\(\d+: No such file or directory\)|directory index of .* is forbidden|"
                   r"Software caused connection abort|Connection reset by peer|client prematurely closed", re.I)


def noise(msg):
    return bool(NOISE.search(msg))


def errlog_groups(db, site, start, end):
    """{(level, message with numbers as N): (count, first, last)}; noise is judged on the real text."""
    groups = {}
    for ts, lvl, msg, host, req, n in db.execute(
            "SELECT ts, level, msg, host, request, n FROM errlog WHERE day BETWEEN ? AND ?", (start, end)):
        if host and host not in site.hosts:
            continue
        key = (lvl, msg if noise(msg) else re.sub(r"\d+", "N", msg)[:160])
        if noise(msg):
            key = (lvl, NOISE.search(msg).group(0))
        cnt, first, last = groups.get(key, (0, ts, ts))
        groups[key] = (cnt + n, min(first, ts), max(last, ts))
    return groups


def errors_report(db, site, days):
    q = Q(db, site)
    start, end = days_back(days)
    print(bold(f"{site.name} ({site.domain}) — errors, {days} days") + dim(f"   {span(start, end)}"))
    h1("To do", start, end)
    error_summary(db, q, site, start, end)

    h1("Server errors (5xx)")
    rows = q.rows("""SELECT sum(n) s, host || path, status, max(ts) FROM hits WHERE {site} AND kind='error'
        AND day BETWEEN ? AND ? GROUP BY host, path, status ORDER BY s DESC LIMIT 15""", (start, end))
    table([(s, p, f"{st}, last {time.strftime('%m-%d %H:%M', time.localtime(t))}") for s, p, st, t in rows])

    h1("Broken links on own pages (404, the link is on the page in grey)")
    table([(n, path, page) for n, path, page, _ in broken_links(q, site, start, end)[:20]])

    h1("Pages not found, linked from outside (old addresses worth a redirect?)")
    rows = q.rows(f"""SELECT sum(n) s, path, group_concat(DISTINCT referer) FROM hits WHERE {{site}}
        AND kind='notfound' AND day BETWEEN ? AND ? AND referer NOT LIKE '%{site.domain}/%'
        GROUP BY path ORDER BY s DESC""", (start, end))
    # scanners send no referrer: a page with one was linked from somewhere
    pages = [(s, p, ref_list(r, site)) for s, p, r in rows if ref_list(r, site)
             and (p.endswith("/") or p.endswith(".html") or "." not in p.rsplit("/", 1)[-1])]
    table(pages[:15])
    files = [(s, p, ref_list(r, site)) for s, p, r in rows
             if re.search(r"\.(jpe?g|png|gif|webp|svg|pdf|xlsx?|csv|zip)$", p, re.I)]
    if files:
        h1("Files not found (/pub/, /assets/)")
        table(files[:10])

    h1("nginx error.log")
    groups = errlog_groups(db, site, start, end)
    real = sorted(((v, k) for k, v in groups.items() if not noise(k[1])), reverse=True)
    if not real:
        print(green("  nothing but scans and rate limits"))
    for (n, first, last), (lvl, msg) in real[:15]:
        col = red if lvl in ("crit", "alert", "emerg", "error") else yellow
        print(f"  {fmt_n(n):>7} {col(f'{lvl:<6}')} {cut(msg, WIDTH - 50)}  {dim(when(last))}")
    nz = sorted(((v[0], k[1]) for k, v in groups.items() if noise(k[1])), reverse=True)
    if nz:
        print(dim("  scans and limits: " + ", ".join(f"{cut(m, 40)} {fmt_n(n)}" for n, m in nz[:4])))

    h1("Monitor (warnings, errors, restarts)")
    rows = db.execute("""SELECT level, msg, sum(n), min(ts), max(ts) FROM monitor WHERE day BETWEEN ? AND ?
        AND level IN ('ERROR','WARN','FAIL','guard:','mail') GROUP BY level, msg ORDER BY max(ts) DESC LIMIT 20""",
                      (start, end)).fetchall()
    if not rows:
        print(green("  all checks OK"))
    for lvl, msg, n, first, last in rows:
        col = red if lvl in ("ERROR", "FAIL") else yellow
        when = time.strftime('%m-%d %H:%M', time.localtime(last))
        if n > 1:
            when = f"{time.strftime('%m-%d %H:%M', time.localtime(first))}…{when}"
        print(f"  {n:>4}× {col(f'{lvl:<6}')} {cut(msg, WIDTH - 50)}  {dim(when)}")
    last = db.execute("SELECT max(ts) FROM monitor").fetchone()[0]
    if last:
        print(dim(f"  monitor last ran {time.strftime('%m-%d %H:%M', time.localtime(last))}"))

    h1("Scans and bad requests blocked")
    table([(s, p or "(no path: garbage request)") for s, p in q.rows(
        """SELECT sum(n) s, path FROM hits WHERE {site} AND kind='probe' AND day BETWEEN ? AND ?
        GROUP BY path ORDER BY s DESC LIMIT 8""", (start, end))])


def ref_list(refs, site):
    """The outside sources among the referrers. The site itself is not one: a 404 from an own
    page is a broken link (broken_links), and scanners send the bare domain as the referrer."""
    names = sorted({ref_name(r, site) for r in (refs or "").split(",") if r})
    names = [n for n in names if n not in ("(direct, apps, bookmarks)", "(own pages)")]
    return ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")


def check_report(db, site, days):
    """How requests were counted: lets you see what the rules call people, bots and the rest."""
    q = Q(db, site)
    start, end = days_back(days)
    print(bold(f"{site.name} — how requests were counted, {days} days") + dim(f"   {span(start, end)}   rules v{RULES} in ship/stats.py classify()"))

    h1("Kinds")
    for k, n in q.rows("""SELECT kind, sum(n) s FROM hits WHERE {site} AND day BETWEEN ? AND ?
            GROUP BY kind ORDER BY s DESC""", (start, end)):
        print(f"  {k:<9} {fmt_n(n):>9}  {dim(KINDS.get(k, ''))}")

    h1("Counted as people, but the page is not in the current build (wrong rule? removed page?)")
    rows = q.rows(f"""SELECT sum(n) s, path FROM hits WHERE {{site}} AND kind IN ('read','read?','hit')
        AND day BETWEEN ? AND ? GROUP BY path ORDER BY s DESC""", (start, end))
    table([r for r in rows if r[1] not in site.pages and unquote(r[1]) not in site.pages][:15])

    h1("People, unconfirmed (read?): their agents")
    table(q.rows("""SELECT sum(n) s, ua FROM hits WHERE {site} AND kind='read?' AND day BETWEEN ? AND ?
        GROUP BY ua ORDER BY s DESC LIMIT 10""", (start, end)))

    h1("Counted as bots: their agents")
    table(q.rows("""SELECT sum(n) s, ua FROM hits WHERE {site} AND kind='bot' AND day BETWEEN ? AND ?
        GROUP BY ua ORDER BY s DESC LIMIT 15""", (start, end)))

    h1("Front page: who asks for it")
    table(q.rows("""SELECT sum(n) s, kind || '  ' || CASE WHEN ua='' THEN '(no agent in httpd logs)' ELSE ua END
        FROM hits WHERE {site} AND path='/' AND method='GET' AND status IN (200,304) AND day BETWEEN ? AND ?
        GROUP BY kind, ua ORDER BY s DESC LIMIT 10""", (start, end)))

    h1("Not counted (other): paths")
    table(q.rows("""SELECT sum(n) s, method || ' ' || path || CASE WHEN query != '' THEN '?' || query ELSE '' END
        || '  ' || status FROM hits WHERE {site} AND kind='other' AND day BETWEEN ? AND ?
        GROUP BY method, path, query, status ORDER BY s DESC LIMIT 15""", (start, end)))

    h1("Pages in the build nobody read")
    read = {r[1] for r in rows}
    unread = sorted(p for p in site.pages if p not in read and not p.startswith(("/tag", "/page")))
    print("  " + (", ".join(unread[:30]) + (f" … and {len(unread) - 30} more" if len(unread) > 30 else "")
                  if unread else dim("(none)")))


# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["import", "dashboard", "check", "errors"])
    ap.add_argument("stats_dir")
    ap.add_argument("--site")
    ap.add_argument("--domain")
    ap.add_argument("--comments-host", default="")
    ap.add_argument("--build", help="the site's built folder (z-public/prod): its pages and titles")
    ap.add_argument("--other-build", action="append", help="other sites on the same server")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    if not a.domain or (a.command != "import" and not a.site):
        ap.error("--domain (and --site, except for import) are needed")
    t0 = time.time()
    db, done = run_import(a.stats_dir, a.comments_host, (a.domain or "").lower(), quiet=a.command != "import")
    if a.command == "import":
        print(f"  {done} files imported in {time.time() - t0:.0f} s")
        return
    if done:
        print(dim(f"({done} new log files imported)"))
    site = Site(a)
    {"dashboard": dashboard, "check": check_report, "errors": errors_report}[a.command](db, site, a.days)
    print()


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
