#!/usr/bin/env python
"""ilan.gov.tr - Akademik Personel Alimlari (kategori 73) ilanlarini ve detaylarini
Playwright (gercek tarayici) ile cekip SQLite'a yazar.

Kurulum:
    pip install playwright beautifulsoup4
    python -m playwright install chromium

Kullanim:
    python ilan_cek.py                    # yeni ilanlari ekler, mevcut detaylari atlar
    python ilan_cek.py --refresh          # tum detaylari yeniden ceker
    python ilan_cek.py --headed           # tarayiciyi gorunur ac
    python ilan_cek.py --db baska.db --delay 1.0
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

BASE = "https://www.ilan.gov.tr"
LIST_URL = BASE + "/ilan/kategori/73/akademik-personel-alimlari?currentPage={page}"
TIMEOUT = 45_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS ilanlar (
    id                  INTEGER PRIMARY KEY,
    ilan_no             TEXT,
    baslik              TEXT,
    kurum               TEXT,
    kurum_kodu          TEXT,
    kaynak              TEXT,
    il                  TEXT,
    ilce                TEXT,
    yayin_tarihi        TEXT,   -- listeden (UTC ISO)
    resmi_gazete_tarihi TEXT,
    kategori            TEXT,   -- "A > B > C"
    alt_kategori        TEXT,   -- listedeki 'Kategori' degeri
    goruntulenme        INTEGER,
    arsivlendi          INTEGER,
    url                 TEXT,
    icerik_html         TEXT,
    icerik_metin        TEXT,
    liste_json          TEXT,
    detay_json          TEXT,
    detay_cekildi       TEXT,   -- detayin cekildigi zaman (UTC)
    ilk_gorulme         TEXT,
    son_gorulme         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ilanlar_kurum ON ilanlar(kurum);
CREATE INDEX IF NOT EXISTS ix_ilanlar_il ON ilanlar(il);
CREATE INDEX IF NOT EXISTS ix_ilanlar_yayin ON ilanlar(yayin_tarihi);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def html_to_text(html):
    """HTML'i duz metne cevirir; tablolari satir satir, hucreleri ' | ' ile ayirarak korur."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # bs4 yoksa basit temizlik
        text = re.sub(r"<br\s*/?>|</(p|div|tr|li)>", "\n", html, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        return re.sub(r"\n\s*\n+", "\n\n", text.replace("\xa0", " ")).strip()
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(" ").replace("\xa0", " ")).strip()
                     for c in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows) + "\n")
    text = soup.get_text("\n").replace("\xa0", " ")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def rebuild_texts(db):
    """Mevcut kayitlarin duz metnini (tablo duzeltmesi icin) HTML'den yeniden uretir."""
    rows = db.execute("SELECT id, icerik_html FROM ilanlar WHERE icerik_html IS NOT NULL").fetchall()
    db.executemany("UPDATE ilanlar SET icerik_metin=? WHERE id=?",
                   [(html_to_text(h), i) for i, h in rows])
    db.commit()


def fetch_listing(page):
    """Liste sayfalarini gezip tum ilan ozetlerini toplar."""
    ads = {}
    total = None
    n = 1  # site 0 ve 1 icin ayni (ilk) sayfayi doner
    while True:
        try:
            with page.expect_response(
                lambda r: "Ad/AdsByFilter" in r.url and r.request.method == "POST",
                timeout=TIMEOUT,
            ) as info:
                page.goto(LIST_URL.format(page=n), wait_until="domcontentloaded")
            res = info.value.json()["result"]
        except PWTimeout:
            print(f"  ! sayfa {n}: liste yaniti gelmedi, duruyorum", file=sys.stderr)
            break
        batch = res.get("ads") or []
        if total is None:
            total = res.get("totalCount") or res.get("total") or res.get("count")
        new = [a for a in batch if a["id"] not in ads]
        for a in batch:
            ads[a["id"]] = a
        print(f"  sayfa {n}: {len(batch)} ilan (toplam {len(ads)}/{total or '?'})")
        if not batch or not new or (total and len(ads) >= total):
            break
        n += 1
        time.sleep(0.5)
    return list(ads.values())


def fetch_detail(page, ad):
    url = BASE + ad["urlStr"]
    with page.expect_response(
        lambda r: "AdDetail/GetAdDetail" in r.url, timeout=TIMEOUT
    ) as info:
        page.goto(url, wait_until="domcontentloaded")
    return info.value.json()["result"]


def kv(items, key_part):
    for it in items or []:
        if key_part.lower() in (it.get("key") or "").lower():
            return it.get("value")
    return None


def upsert_list(db, ad):
    t = now()
    db.execute(
        """INSERT INTO ilanlar (id, ilan_no, baslik, kurum, kaynak, il, ilce, yayin_tarihi,
               resmi_gazete_tarihi, alt_kategori, arsivlendi, url, liste_json,
               ilk_gorulme, son_gorulme)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               baslik=excluded.baslik, kurum=excluded.kurum, il=excluded.il,
               ilce=excluded.ilce, yayin_tarihi=excluded.yayin_tarihi,
               resmi_gazete_tarihi=excluded.resmi_gazete_tarihi,
               alt_kategori=excluded.alt_kategori, arsivlendi=excluded.arsivlendi,
               url=excluded.url, liste_json=excluded.liste_json,
               son_gorulme=excluded.son_gorulme""",
        (
            int(ad["id"]), ad.get("adNo"), ad.get("title"), ad.get("advertiserName"),
            ad.get("adSourceName"), ad.get("addressCityName"), ad.get("addressCountyName"),
            ad.get("publishStartDate"), kv(ad.get("adTypeFilters"), "Gazete"),
            kv(ad.get("values"), "Kategori"), int(bool(ad.get("isArchived"))),
            BASE + ad["urlStr"], json.dumps(ad, ensure_ascii=False), t, t,
        ),
    )


def save_detail(db, d):
    cats = " > ".join(c["name"] for c in d.get("categories") or [])
    html = d.get("content") or ""
    db.execute(
        """UPDATE ilanlar SET ilan_no=?, baslik=?, kurum=?, kurum_kodu=?, kaynak=?, il=?, ilce=?,
               kategori=?, goruntulenme=?, arsivlendi=?, icerik_html=?, icerik_metin=?,
               detay_json=?, detay_cekildi=? WHERE id=?""",
        (
            d.get("adNo"), d.get("title"), d.get("advertiserName"), d.get("advertiserCode"),
            d.get("adSourceName"), d.get("addressCityName"), d.get("addressCountyName"),
            cats, d.get("hitCount"), int(bool(d.get("isArchived"))), html,
            html_to_text(html), json.dumps(d, ensure_ascii=False), now(), int(d["id"]),
        ),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="ilanlar.db")
    ap.add_argument("--refresh", action="store_true", help="mevcut detaylari da yeniden cek")
    ap.add_argument("--headed", action="store_true", help="tarayiciyi gorunur ac")
    ap.add_argument("--delay", type=float, default=0.7, help="detay istekleri arasi bekleme (sn)")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.executescript(SCHEMA)
    rebuild_texts(db)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(locale="tr-TR", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT)

        print("Liste cekiliyor...")
        ads = fetch_listing(page)
        for ad in ads:
            upsert_list(db, ad)
        db.commit()
        print(f"{len(ads)} ilan listeden alindi.")

        if args.refresh:
            todo = [a for a in ads]
        else:
            done = {r[0] for r in db.execute("SELECT id FROM ilanlar WHERE detay_cekildi IS NOT NULL")}
            todo = [a for a in ads if int(a["id"]) not in done]
        print(f"{len(todo)} ilanin detayi cekilecek.")

        failed = []
        for i, ad in enumerate(todo, 1):
            try:
                d = fetch_detail(page, ad)
                save_detail(db, d)
                db.commit()
                print(f"  [{i}/{len(todo)}] {ad['id']} {ad['title'][:70]}")
            except Exception as e:  # noqa: BLE001
                failed.append(ad["id"])
                print(f"  [{i}/{len(todo)}] {ad['id']} HATA: {e}", file=sys.stderr)
            time.sleep(args.delay)
        browser.close()

    n = db.execute("SELECT COUNT(*), COUNT(detay_cekildi) FROM ilanlar").fetchone()
    print(f"Bitti. Veritabaninda {n[0]} ilan, {n[1]} tanesinin detayi var. Basarisiz: {len(failed)}")
    if failed:
        print("Basarisiz id'ler (tekrar calistirinca denenir):", ", ".join(failed))
    import parse_kadro
    print(f"Kadro tablolari ayristiriliyor... {parse_kadro.parse_all(db)} kadro satiri.")
    db.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
