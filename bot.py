#!/usr/bin/env python3
# Monitor competitor: ogni giorno sottodomini, landing e ads Meta, confronto con ieri.
# Scrive data/latest.json e data/history.json per l'app (index.html su GitHub Pages).
import datetime as dt
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).parent
DATA = ROOT / "data"
STATE = DATA / "state.json"
DOMAIN = os.environ.get("TARGET_DOMAIN", "cantinhodetudo.online").strip().lower()
TODAY = dt.datetime.now(dt.timezone.utc).date().isoformat()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
INFRA = {"api", "app", "chat", "n8", "n8webhook", "minio", "s3", "pay", "painel", "mail", "flow", "quiz",
         "lp", "go", "type", "rbac", "www", "admin"}
THANKS = {"obrigado", "gracias", "grazie", "merci", "multumim", "thanks", "danke"}


def log(*a):
    print("[" + dt.datetime.now().strftime("%H:%M:%S") + "]", *a, flush=True)


def get(url, timeout=30):
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 429:
                time.sleep(10 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            log("errore rete", url[:80], e)
            time.sleep(3 * (i + 1))
    return None


def sub_of(host):
    host = host.lower().strip().lstrip("*.").rstrip(".")
    if host == DOMAIN:
        return "(root)"
    if host.endswith("." + DOMAIN):
        return host[: -len(DOMAIN) - 1]
    return None


def full(sub):
    return DOMAIN if sub == "(root)" else sub + "." + DOMAIN


def classify(sub):
    if sub in INFRA or re.search(r"(^|-)(qa|test|teste)", sub):
        return "Infrastruttura/test"
    if sub in THANKS:
        return "Thank-you page"
    if re.search(r"\d{2,3}$", sub):
        return "Upsell/downsell"
    return "Landing"


# ------------------------------------------------------------ sottodomini
def fetch_subdomains():
    subs, after = {}, None
    for _ in range(50):
        url = ("https://api.certspotter.com/v1/issuances?domain=" + DOMAIN +
               "&include_subdomains=true&expand=dns_names" + ("&after=" + after if after else ""))
        r = get(url)
        if r is None or r.status_code != 200:
            log("Cert Spotter non risponde", getattr(r, "status_code", ""))
            break
        batch = r.json()
        if not batch:
            break
        for c in batch:
            for name in c.get("dns_names", []):
                s = sub_of(name)
                if s is None:
                    continue
                nb = c.get("not_before", "")[:10]
                if s not in subs or nb < subs[s]:
                    subs[s] = nb
        after = batch[-1]["id"]
    r = get("https://api.hackertarget.com/hostsearch/?q=" + DOMAIN)
    if r is not None and r.status_code == 200 and "," in r.text:
        for line in r.text.splitlines():
            s = sub_of(line.split(",")[0])
            if s and s not in subs:
                subs[s] = ""
    log(len(subs), "sottodomini")
    return subs


# ------------------------------------------------------------ landing
PRICE_RE = re.compile(r"(?:US\$|R\$|\$|€|£)\s?\d{1,4}(?:[.,]\d{2})?|\d{1,4}(?:[.,]\d{2})?\s?(?:€|lei|RON)")


def scan(sub):
    r = get("https://" + full(sub) + "/", timeout=20)
    if r is None:
        return {"status": "errore"}
    html = r.text or ""
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
    return {
        "status": r.status_code,
        "title": (t.group(1).strip() if t else "")[:160],
        "prices": sorted(set(p.replace(" ", "") for p in PRICE_RE.findall(text)))[:20],
        "hotmart": sorted(set(re.findall(r"pay\.hotmart\.com/([A-Z0-9]{8,14})", html))),
        "hash": hashlib.sha1(text[:20000].encode()).hexdigest()[:12],
    }


# ------------------------------------------------------------ ads (Libreria inserzioni, senza token)
MONTHS = {"gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9, "ott": 10,
          "nov": 11, "dic": 12, "jan": 1, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "dec": 12}
DATE_RE = re.compile(r"(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})|([A-Za-z]{3})[a-z]* (\d{1,2}), (\d{4})")


def dates(line):
    out = []
    for m in DATE_RE.finditer(line):
        if m.group(1):
            d, mo, y = m.group(1), m.group(2), m.group(3)
        else:
            mo, d, y = m.group(4), m.group(5), m.group(6)
        mo = MONTHS.get(mo.lower()[:3])
        if mo:
            out.append("%s-%02d-%02d" % (y, mo, int(d)))
    return out


def parse_library(text):
    parts = re.split(r"ID libreria:|Library ID:", text)
    ads = {}
    for i in range(1, len(parts)):
        prev = [l.strip() for l in parts[i - 1].strip().splitlines() if l.strip() and l.strip() != "\u200b"]
        active = bool(prev) and prev[-1] in ("Attiva", "Active")
        lines = [l.strip() for l in parts[i].splitlines() if l.strip() and l.strip() != "\u200b"]
        if not lines:
            continue
        ad_id = re.sub(r"\D", "", lines[0])
        if not ad_id:
            continue
        dl = next((l for l in lines[:6] if dates(l)), "")
        ds = dates(dl)
        start = ds[0] if ds else ""
        stop = "" if active else (ds[1] if len(ds) > 1 else start)
        sp = next((k for k, l in enumerate(lines) if l in ("Sponsorizzato", "Sponsored")), -1)
        cap = next((l for l in lines if DOMAIN.upper() in l.upper()), "")
        ci = lines.index(cap) if cap else -1
        body = []
        if sp >= 0:
            for l in lines[sp + 1:]:
                if l == cap or re.match(r"^\d+:\d+ / \d+:\d+$", l) or l.startswith(("Scarica", "Download")):
                    break
                body.append(l)
        prod = None
        if cap:
            m = re.search(r"([a-z0-9-]+\.)?" + re.escape(DOMAIN), cap.lower())
            prod = sub_of(m.group(0)) if m else None
        ads[ad_id] = {"id": ad_id, "page": lines[sp - 1] if sp > 0 else "", "start": start, "stop": stop,
                      "active": active, "product": prod,
                      "title": (lines[ci + 1] if 0 <= ci < len(lines) - 1 else "")[:120],
                      "body": " ".join(body)[:400]}
    by_body = {a["body"][:80]: a["product"] for a in ads.values() if a["product"] and a["body"]}
    for a in ads.values():
        if not a["product"]:
            a["product"] = by_body.get(a["body"][:80], "?")
    return ads


def fetch_ads():
    from playwright.sync_api import sync_playwright
    url = ("https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL"
           "&media_type=all&search_type=keyword_unordered&q=" + DOMAIN)
    text = ""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_context(locale="it-IT", user_agent=UA["User-Agent"],
                             viewport={"width": 1366, "height": 900}).new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(6000)
            for label in ("Rifiuta cookie facoltativi", "Decline optional cookies",
                          "Consenti solo i cookie essenziali", "Only allow essential cookies"):
                btn = page.get_by_role("button", name=label)
                if btn.count():
                    btn.first.click()
                    page.wait_for_timeout(1500)
                    break
            last, same = -1, 0
            for _ in range(150):
                page.mouse.wheel(0, 20000)
                page.wait_for_timeout(1800)
                n = page.evaluate("(document.body.innerText.match(/ID libreria:|Library ID:/g)||[]).length")
                if n == last:
                    same += 1
                    if same >= 5:
                        break
                else:
                    last, same = n, 0
            text = page.evaluate("document.body.innerText")
        except Exception as e:
            log("Libreria non leggibile:", e)
        finally:
            b.close()
    ads = parse_library(text)
    log(len(ads), "ads lette")
    return ads or None


# ------------------------------------------------------------ confronto con ieri
def item_ad(a):
    return {"label": (a["page"] + " — " + (a["title"] or a["body"][:70])).strip(" —"), "id": a["id"],
            "product": a["product"], "start": a["start"]}


def diff(prev, cur):
    ch = defaultdict(list)
    ps, cs = prev.get("subdomains", {}), cur["subdomains"]
    for s in sorted(set(cs) - set(ps)):
        l = cur["landings"].get(s, {})
        ch["nuovi_sottodomini"].append({"label": s + " · " + classify(s) + (" · " + l["title"] if l.get("title") else ""),
                                        "sub": s, "prices": l.get("prices", [])})
    for s in sorted(set(ps) - set(cs)):
        ch["sottodomini_spariti"].append({"label": s})
    pl, cl = prev.get("landings", {}), cur["landings"]
    for s, l in cl.items():
        o = pl.get(s)
        if not o or s not in ps:
            continue
        if o.get("prices") != l.get("prices"):
            ch["prezzi_cambiati"].append({"label": s + ": " + " ".join(o.get("prices") or []) + " → " +
                                          " ".join(l.get("prices") or []), "sub": s})
        if o.get("hotmart") != l.get("hotmart"):
            ch["checkout_cambiati"].append({"label": s + ": checkout Hotmart cambiato", "sub": s})
        if o.get("title") != l.get("title"):
            ch["titoli_cambiati"].append({"label": s + ": " + str(o.get("title")) + " → " + str(l.get("title")), "sub": s})
        elif o.get("hash") != l.get("hash"):
            ch["landing_modificate"].append({"label": s, "sub": s})
        if o.get("status") == 200 and l.get("status") != 200:
            ch["landing_offline"].append({"label": s + " (HTTP " + str(l.get("status")) + ")", "sub": s})
    pa, ca = prev.get("ads"), cur.get("ads")
    if pa is not None and ca is not None:
        for i in sorted(set(ca) - set(pa)):
            ch["ads_nuove"].append(item_ad(ca[i]))
        for i in sorted(set(ca) & set(pa)):
            if pa[i]["active"] and not ca[i]["active"]:
                ch["ads_spente"].append(item_ad(ca[i]))
            elif not pa[i]["active"] and ca[i]["active"]:
                ch["ads_riaccese"].append(item_ad(ca[i]))
        for p in sorted({a["page"] for a in ca.values()} - {a["page"] for a in pa.values()}):
            ch["pagine_fb_nuove"].append({"label": p})
        for p in sorted({a["product"] for a in ca.values()} - {a["product"] for a in pa.values()}):
            ch["prodotti_nuovi_in_ads"].append({"label": p, "sub": p if p != "?" else None})
    return ch


def products(ads):
    g = defaultdict(lambda: {"ads": 0, "active": 0, "first": "9999", "last": "", "pages": set(), "title": "", "new_today": 0})
    for a in ads.values():
        x = g[a["product"]]
        x["ads"] += 1
        x["active"] += a["active"]
        if a["start"] and a["start"] < x["first"]:
            x["first"] = a["start"]
        x["last"] = max(x["last"], TODAY if a["active"] else (a["stop"] or a["start"]))
        x["pages"].add(a["page"])
        x["title"] = x["title"] or a["title"]
        x["new_today"] += a["start"] == TODAY
    return [{"sub": k, "title": v["title"], "pages": sorted(v["pages"]), "ads": v["ads"], "active": v["active"],
             "first": v["first"], "last": "" if v["active"] else v["last"], "new_today": v["new_today"]}
            for k, v in g.items()]


def main():
    DATA.mkdir(exist_ok=True)
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    subs = fetch_subdomains() or prev.get("subdomains", {})
    landings = {}
    for s in sorted(subs):
        if classify(s) != "Infrastruttura/test":
            landings[s] = scan(s)
            time.sleep(0.3)
    try:
        ads = fetch_ads()
    except Exception as e:
        log("ads non disponibili:", e)
        ads = None
    ads_ok = ads is not None
    if not ads_ok:
        ads = prev.get("ads")
    cur = {"date": TODAY, "subdomains": subs, "landings": landings, "ads": ads}
    first = not prev
    ch = {} if first else diff(prev, cur if ads_ok else {**cur, "ads": None})
    allads = ads or {}
    totals = {"ads": len(allads), "attive": sum(a["active"] for a in allads.values()), "subs": len(subs),
              "ads_aggiornate": ads_ok}
    latest = {"date": TODAY, "domain": DOMAIN, "totals": totals, "products": products(allads),
              "subdomains": [{"sub": s, "type": classify(s), "cert": d, **{k: landings.get(s, {}).get(k, "")
                              for k in ("title", "status")}, "prices": landings.get(s, {}).get("prices", []),
                              "hotmart": landings.get(s, {}).get("hotmart", [])} for s, d in sorted(subs.items())],
              "ads": sorted(allads.values(), key=lambda a: a["start"], reverse=True)}
    for s in latest["subdomains"]:
        s["http"] = s.pop("status")
    (DATA / "latest.json").write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
    hf = DATA / "history.json"
    hist = [h for h in (json.loads(hf.read_text()) if hf.exists() else []) if h.get("date") != TODAY]
    hist.append({"date": TODAY, "baseline": first, "totals": totals,
                 "changes": {k: {"count": len(v), "items": v[:60]} for k, v in ch.items()},
                 "analysis": "" if ads_ok else "Oggi Facebook non ha lasciato leggere la Libreria inserzioni: "
                                               "le ads mostrate sono quelle dell'ultimo controllo riuscito."})
    hf.write_text(json.dumps(sorted(hist, key=lambda h: h["date"])[-180:], ensure_ascii=False), encoding="utf-8")
    STATE.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    log("fatto:", {k: len(v) for k, v in ch.items()})


if __name__ == "__main__":
    main()
