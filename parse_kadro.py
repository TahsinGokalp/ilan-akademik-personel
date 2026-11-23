#!/usr/bin/env python
"""ilanlar.icerik_html icindeki kadro tablolarini ayristirip `kadrolar` tablosuna yazar.

Her tablo satiri bir kadro (pozisyon) olur: birim, bolum, anabilim dali, unvan, kadro sayisi,
derece, ALES / yabanci dil puanlari, ilan sartlari.

    python parse_kadro.py [--db ilanlar.db] [--rapor]

`kadrolar` turetilmis veridir: her calistirmada ilanlar tablosundan bastan uretilir
(ilanlar tablosuna dokunulmaz).
"""
import argparse
import json
import re
import sqlite3
import sys

from bs4 import BeautifulSoup

SCHEMA = """
CREATE TABLE IF NOT EXISTS kadrolar (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ilan_id            INTEGER NOT NULL REFERENCES ilanlar(id),
    tablo_no           INTEGER,
    satir_no           INTEGER,
    pozisyon_no        TEXT,
    birim              TEXT,
    bolum              TEXT,
    anabilim_dali      TEXT,
    uzmanlik           TEXT,
    unvan              TEXT,
    unvan_grup         TEXT,
    kadro_sayisi       INTEGER,
    derece             TEXT,
    ales_puani         TEXT,
    ales_puan_turu     TEXT,
    yabanci_dil_puani  TEXT,
    ales_min           REAL,
    dil_min            REAL,
    sartlar            TEXT,
    aciklama           TEXT,
    ham_json           TEXT,
    ara                TEXT,   -- aranabilir alan (kurum/il/birim/bolum/abd/unvan), Turkce harfleri sadelestirilmis
    ara_sart           TEXT,   -- sartlar + aciklama + uzmanlik, sadelestirilmis
    bolum_ad           TEXT,   -- secim listesinde gorunen bolum adi (bolum > abd > birim)
    bolum_key          TEXT    -- normallestirilmis bolum anahtari (gruplama)
);
CREATE INDEX IF NOT EXISTS ix_kadro_ilan ON kadrolar(ilan_id);
CREATE INDEX IF NOT EXISTS ix_kadro_unvan ON kadrolar(unvan_grup);
CREATE INDEX IF NOT EXISTS ix_kadro_bolum ON kadrolar(bolum_key);
"""

_TR = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ç": "c", "ç": "c",
                     "Ğ": "g", "ğ": "g", "Ö": "o", "ö": "o", "Ü": "u", "ü": "u",
                     "Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u"})


def bolum_adi(r):
    """Secim listesi icin bolum adi: bolum, yoksa anabilim dali, yoksa birim."""
    name = clean(r["bolum"] or r["anabilim_dali"] or r["birim"])
    return re.sub(r"[*]+", "", name).strip(" -/,")


def bolum_anahtar(name):
    """'Pazarlama Bolumu', 'PAZARLAMA*' gibi yazim farklarini ayni anahtarda toplar."""
    k = fold(re.sub(r"\(.*?\)", " ", name))
    k = re.sub(r"[^a-z0-9 ]+", " ", k)
    k = re.sub(r"(bolumu|bolum|lisans programi|programi|program|ana bilim dali|anabilim dali)", " ", k)
    return re.sub(r"\s+", " ", k).strip()


def fold(s):
    """Kucuk harf + Turkce karakterleri ASCII'ye indirger (arama icin)."""
    return re.sub(r"\s+", " ", (s or "").translate(_TR).lower()).strip()


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


# ---------- baslik eslemesi ----------
def map_header(h):
    f = fold(h)
    if not f:
        return None
    if "ales" in f:
        return "ales_turu" if "tur" in f else "ales"
    if "yabanci dil" in f or "yds" in f or "yokdil" in f or "yab.dil" in f or "yab dil" in f:
        return "dil"
    if "uzmanlik" in f:
        return "uzmanlik"
    if "unvan" in f:
        return "unvan"
    if re.search(r"\b(adet|adedi|adt|sayi|sayisi|ad)\b", f) or "kadro sayi" in f or "adet" in f:
        return "adet"
    if re.search(r"derece|\bdrc\b|\bder\b|k/d|kadro der", f):
        return "derece"
    if re.fullmatch(r"(ilan |ilan sira |sira |s\.?n\.?|)(no|kodu|numarasi|sira no|sira numarasi)\.?", f) or f in ("s.n.", "no"):
        return "no"
    has_bolum = "bolum" in f or "program" in f
    has_abd = "anabilim" in f or re.search(r"\babd\b|a\.b\.d|\basd\b|bilim dali|sanat dali", f)
    has_birim = re.search(r"fakulte|birim|yuksekokul|enstitu|meslek", f)
    if has_birim and (has_bolum or has_abd):
        return "birim"  # birlesik: birim/bolum/abd tek hucrede
    if has_bolum and has_abd:
        return "bolum"
    if has_abd and not has_bolum:
        return "abd"
    if has_bolum:
        return "bolum"
    if has_birim:
        return "birim"
    if "aciklama" in f:
        return "aciklama"
    if re.search(r"sart|kosul|aranan|nitelik|kriter", f):
        return "sart"
    return None


def expand_table(table):
    """rowspan/colspan'i acip duz bir hucre izgarasi (list[list[str]]) dondurur."""
    grid, pending = [], {}
    for tr in table.find_all("tr"):
        row, col = [], 0
        cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
        it = iter(cells)
        while True:
            while col in pending:
                text, left = pending[col]
                row.append(text)
                if left <= 1:
                    del pending[col]
                else:
                    pending[col] = (text, left - 1)
                col += 1
            c = next(it, None)
            if c is None:
                break
            text = clean(c.get_text(" "))
            cs = int(re.sub(r"\D", "", c.get("colspan", "1")) or 1)
            rs = int(re.sub(r"\D", "", c.get("rowspan", "1")) or 1)
            for k in range(cs):
                row.append(text)
                if rs > 1:
                    pending[col + k] = (text, rs - 1)
            col += cs
        while col in pending:  # satir sonunda kalan rowspan hucreleri
            text, left = pending[col]
            row.append(text)
            if left <= 1:
                del pending[col]
            else:
                pending[col] = (text, left - 1)
            col += 1
        if row:
            grid.append(row)
    return grid


def header_score(row):
    """Satir bir baslik satiri ise (eslesen farkli alan sayisi), degilse 0 dondurur."""
    m = [map_header(c) for c in row]
    known = {x for x in m if x}
    if len(known) >= 3 and len(known & {"unvan", "adet", "sart", "birim", "bolum", "abd"}) >= 2             and known & {"unvan", "adet", "birim", "bolum", "abd"}:
        return len(known), m
    return 0, None


def find_header(grid):
    """Ilk 6 satir icinde en cok alan eslesen satiri baslik secer (grup basliklarini atlamak icin)."""
    best = (0, None, None)
    for i, row in enumerate(grid[:6]):
        sc, m = header_score(row)
        if sc > best[0]:
            best = (sc, i, m)
    return (best[1], best[2]) if best[0] else (None, None)


# ---------- unvan / puan ----------
def unvan_grup(*texts):
    f = fold(" ".join(t for t in texts if t))
    found = []
    if re.search(r"dr\.? ?ogr\.? ?uye|doktor ogretim uye", f):
        found.append("Dr. Öğr. Üyesi")
    if re.search(r"docent|\bdoc\b|\bdoc\.", f):
        found.append("Doçent")
    if re.search(r"profesor|\bprof\b|\bprof\.", f):
        found.append("Profesör")
    if re.search(r"ogretim gorevlisi|ogr\.? ?gor", f):
        found.append("Öğretim Görevlisi")
    if re.search(r"arastirma gorevlisi|\bar(?:as|s)?\.? ?gor|\barg\b", f):
        found.append("Araştırma Görevlisi")
    if "okutman" in f:
        found.append("Okutman")
    if re.search(r"\buzman\b", f) and "uzmanlik" not in f.replace("uzman ", "uzman"):
        found.append("Uzman")
    if not found and re.search(r"ogretim uye", f):
        found.append("Öğretim Üyesi")
    return ", ".join(found)


def to_int(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def num(s, lo=20, hi=100):
    for m in re.finditer(r"\d{2,3}(?:[.,]\d+)?", s or ""):
        v = float(m.group().replace(",", "."))
        if lo <= v <= hi:
            return v
    return None


def ales_from_text(f):
    m = re.search(r"ales.{0,150}?en az\s*(\d{2,3})", f) or re.search(r"ales.{0,80}?(\d{2,3})\s*(?:\(|ve ustu|puan)", f)
    v = float(m.group(1)) if m else None
    return v if v and 20 <= v <= 100 else None


def dil_from_text(f):
    m = re.search(r"(?:yds|yokdil|yabanci dil|e-?tep).{0,150}?(?:en az\s*)?(\d{2,3})\s*(?:puan|ve ustu|ve uzeri)", f)
    v = float(m.group(1)) if m else None
    return v if v and 20 <= v <= 100 else None


# ---------- ana ayristirma ----------
def parse_ilan(html, baslik):
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    # Tablo disindaki genel ilan metninden puan: yalnizca metinde tek bir deger geciyorsa kullanilir
    doc = fold(soup.get_text(" "))
    ales_vals = {float(m) for m in re.findall(r"ales.{0,150}?en az\s*(\d{2,3})", doc) if 20 <= float(m) <= 100}
    dil_vals = {float(m) for m in re.findall(
        r"(?:yds|yokdil|yabanci dil).{0,150}?en az\s*(\d{2,3})", doc) if 20 <= float(m) <= 100}
    doc_ales = next(iter(ales_vals)) if len(ales_vals) == 1 else None
    doc_dil = next(iter(dil_vals)) if len(dil_vals) == 1 else None
    for tno, table in enumerate(soup.find_all("table"), 1):
        grid = expand_table(table)
        hi, mapping = find_header(grid)
        if hi is None:
            continue
        ctx_birim = ""
        ctx_unvan = ""
        for r in grid[:hi]:  # baslik ustundeki tek hucreli satir (ör. "DOKTOR ÖĞRETİM ÜYESİ")
            if len({c for c in r if c}) == 1:
                if unvan_grup(r[0]):
                    ctx_unvan = r[0]
                elif len(r[0]) < 80:
                    ctx_birim = r[0]  # ör. "TIP FAKULTESI"
        hdr_fold = [fold(c) for c in grid[hi]]
        for sno, row in enumerate(grid[hi + 1:], 1):
            row = (row + [""] * len(mapping))[: len(mapping)]
            vals = {}
            for key, cell in zip(mapping, row):
                if key:
                    vals[key] = (vals[key] + " " + cell).strip() if key in vals and cell and cell != vals[key] else vals.get(key) or cell
            distinct = {c for c in row if c}
            if not distinct:
                continue
            if sum(1 for a, b in zip(row, hdr_fold) if b and fold(a) == b) >= 3:
                continue  # tekrar eden baslik satiri
            sc, m2 = header_score(row)
            if sc and "unvan" in m2 and all(len(c) < 40 for c in row if c)                     and not any(c.strip().isdigit() for c in row):
                mapping, hdr_fold = m2, [fold(c) for c in row]  # tablo icinde yeni bir baslik blogu basliyor
                continue
            if any(fold(c).startswith("toplam") for c in row if c) and not vals.get("bolum"):
                continue  # TOPLAM satiri
            if len(distinct) == 1 and len(row) > 2 and not vals.get("adet", "").strip().isdigit():
                ctx_birim = next(iter(distinct))  # "TIP FAKULTESI" gibi bolum baslik satiri
                continue
            if not (vals.get("unvan") or vals.get("adet") or vals.get("bolum") or vals.get("abd")):
                continue
            birim = vals.get("birim") or ctx_birim
            sartlar = vals.get("sart", "")
            aciklama = vals.get("aciklama", "")
            ug = unvan_grup(vals.get("unvan"))
            if not vals.get("unvan"):
                vals["unvan"] = ctx_unvan
                ug = unvan_grup(ctx_unvan) or unvan_grup(baslik)
            ftxt = fold(sartlar + " " + aciklama)
            ales_min = num(vals.get("ales")) if vals.get("ales") else (ales_from_text(ftxt) or doc_ales)
            dil_min = num(vals.get("dil")) if vals.get("dil") else (dil_from_text(ftxt) or doc_dil)
            out.append({
                "tablo_no": tno, "satir_no": sno, "pozisyon_no": vals.get("no", ""),
                "birim": birim, "bolum": vals.get("bolum", ""), "anabilim_dali": vals.get("abd", ""),
                "uzmanlik": vals.get("uzmanlik", ""), "unvan": vals.get("unvan", ""), "unvan_grup": ug,
                "kadro_sayisi": to_int(vals.get("adet")) or (1 if vals.get("unvan") else None),
                "derece": vals.get("derece", ""), "ales_puani": vals.get("ales", ""),
                "ales_puan_turu": vals.get("ales_turu", ""), "yabanci_dil_puani": vals.get("dil", ""),
                "ales_min": ales_min, "dil_min": dil_min, "sartlar": sartlar, "aciklama": aciklama,
                "ham": dict(zip([h or f"kolon{i}" for i, h in enumerate(mapping)], row)),
            })
    return out


def parse_all(db):
    db.execute("DROP TABLE IF EXISTS kadrolar")  # turetilmis veri: sema degisse de bastan kurulur
    db.executescript(SCHEMA)
    n = 0
    for ilan_id, baslik, kurum, il, html in db.execute(
            "SELECT id, baslik, kurum, il, icerik_html FROM ilanlar WHERE icerik_html IS NOT NULL").fetchall():
        for r in parse_ilan(html, baslik):
            ara = fold(" ".join([kurum or "", il or "", r["birim"], r["bolum"], r["anabilim_dali"],
                                 r["unvan"], r["unvan_grup"]]))
            bad = bolum_adi(r)
            ara_sart = fold(" ".join([r["sartlar"], r["aciklama"], r["uzmanlik"]]))
            db.execute(
                """INSERT INTO kadrolar (ilan_id, tablo_no, satir_no, pozisyon_no, birim, bolum, anabilim_dali,
                       uzmanlik, unvan, unvan_grup, kadro_sayisi, derece, ales_puani, ales_puan_turu,
                       yabanci_dil_puani, ales_min, dil_min, sartlar, aciklama, ham_json, ara, ara_sart,
                       bolum_ad, bolum_key)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ilan_id, r["tablo_no"], r["satir_no"], r["pozisyon_no"], r["birim"], r["bolum"],
                 r["anabilim_dali"], r["uzmanlik"], r["unvan"], r["unvan_grup"], r["kadro_sayisi"],
                 r["derece"], r["ales_puani"], r["ales_puan_turu"], r["yabanci_dil_puani"], r["ales_min"],
                 r["dil_min"], r["sartlar"], r["aciklama"], json.dumps(r["ham"], ensure_ascii=False),
                 ara, ara_sart, bad, bolum_anahtar(bad)))
            n += 1
    db.commit()
    return n


def report(db):
    q = lambda s: db.execute(s).fetchall()
    print("kadro satiri:", q("SELECT COUNT(*) FROM kadrolar")[0][0],
          "| kadro sayisi toplami:", q("SELECT SUM(kadro_sayisi) FROM kadrolar")[0][0])
    print("kadrosu cikmayan ilan:", [r[0] for r in q(
        "SELECT id FROM ilanlar WHERE id NOT IN (SELECT ilan_id FROM kadrolar)")])
    for col in ("birim", "bolum", "unvan_grup", "sartlar", "kadro_sayisi", "ales_min", "dil_min"):
        print(f"bos {col}:", q(f"SELECT COUNT(*) FROM kadrolar WHERE {col} IS NULL OR {col}=''")[0][0])
    print("unvan dagilimi:", q("SELECT unvan_grup, COUNT(*) FROM kadrolar GROUP BY 1 ORDER BY 2 DESC"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="ilanlar.db")
    ap.add_argument("--rapor", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    print(parse_all(con), "kadro satiri yazildi.")
    if a.rapor:
        report(con)


# ---------- sart metnini maddelere ayirma ----------
_MADDE = re.compile(r"(?:^|\s)(?:\(?\d{1,2}\s*[-–.)]\)?|[•·▪●]|[-–])\s+(?=[^\d\s])")


def sart_maddeleri(text):
    """'1- ... 2- ...', '- ...', ';' ve cumle sonlarina gore sart metnini madde listesine ayirir."""
    t = clean(text)
    if not t:
        return []
    parts = [p.strip() for p in _MADDE.split(t)]
    parts = [q.strip() for p in parts for q in p.split(";") if q.strip()]
    items = []
    for p in parts:
        # cumle sonlari: kisaltmalardan (Dr., Ogr., Prof. ...) sonraki noktada bolme
        pieces, buf = [], ""
        for sent in re.split(r"(?<=[a-zçğıöşü\)\d])\.\s+(?=[A-ZÇĞİÖŞÜ])", p):
            buf = (buf + ". " + sent) if buf else sent
            last = buf.rsplit(" ", 1)[-1]
            if len(last) > 3 or last[:1].isdigit():
                pieces.append(buf)
                buf = ""
        if buf:
            pieces.append(buf)
        items += pieces
    out = []
    for i in items:
        i = i.strip(" ;.,")
        if len(i) > 2:
            out.append(i + ".")
    return out
