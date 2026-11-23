#!/usr/bin/env python
"""ilanlar.db icin web arayuzu + ilan_cek.py'yi tetikleyen kucuk sunucu.
Sadece standart kutuphane kullanir.

    python server.py                 # http://127.0.0.1:8000
    python server.py --port 9000 --db ilanlar.db
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parse_kadro import fold, sart_maddeleri

ROOT = Path(__file__).resolve().parent
PAGE_SIZE = 20
SORTS = {
    "yeni": "yayin_tarihi DESC, id DESC",
    "eski": "yayin_tarihi ASC, id ASC",
    "kurum": "kurum COLLATE NOCASE ASC, yayin_tarihi DESC",
    "goruntulenme": "goruntulenme DESC",
}
LIST_COLS = ("id, ilan_no, baslik, kurum, il, yayin_tarihi, resmi_gazete_tarihi, "
             "alt_kategori, goruntulenme, arsivlendi, url")


class Scraper:
    """ilan_cek.py'yi arka planda calistirir, ciktisini biriktirir."""

    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self.running = False
        self.log = []
        self.returncode = None

    def start(self, refresh):
        with self.lock:
            if self.running:
                return False
            self.running, self.log, self.returncode = True, [], None
        threading.Thread(target=self._run, args=(refresh,), daemon=True).start()
        return True

    def _run(self, refresh):
        cmd = [sys.executable, "-u", str(ROOT / "ilan_cek.py"), "--db", self.db]
        if refresh:
            cmd.append("--refresh")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        try:
            p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace")
            for line in p.stdout:
                self.log.append(line.rstrip())
                del self.log[:-500]
            p.wait()
            self.returncode = p.returncode
        except Exception as e:  # noqa: BLE001
            self.log.append(f"HATA: {e}")
            self.returncode = -1
        finally:
            self.running = False

    def status(self):
        return {"running": self.running, "returncode": self.returncode, "log": self.log[-200:]}


def make_handler(db_path, scraper):
    def query(sql, params=()):
        if not Path(db_path).exists():
            return []
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, params)]
        except sqlite3.OperationalError:  # tablo henuz yok
            return []
        finally:
            con.close()

    def match_clause(column, text):
        """'a b, c' -> (a VE b) VEYA c ; Turkce harfler sadelestirilerek aranir."""
        groups = []
        for grp in text.split(","):
            terms = fold(grp).split()
            if terms:
                groups.append(("(" + " AND ".join(f"{column} LIKE ?" for _ in terms) + ")",
                               [f"%{t}%" for t in terms]))
        if not groups:
            return None, []
        return "(" + " OR ".join(g[0] for g in groups) + ")", [a for g in groups for a in g[1]]

    def search_kadrolar(q):
        where, args = [], []
        for param, col in (("q", "k.ara"), ("sart", "k.ara_sart")):
            if q.get(param):
                clause, a = match_clause(col, q[param])
                if clause:
                    where.append(clause)
                    args += a
        if q.get("unvan"):
            parts = [p for p in q["unvan"].split("|") if p]
            where.append("(" + " OR ".join("k.unvan_grup LIKE ?" for _ in parts) + ")")
            args += [f"%{p}%" for p in parts]
        for param, col in (("il", "i.il"), ("kurum", "i.kurum")):
            if q.get(param):
                where.append(f"{col} = ?")
                args.append(q[param])
        try:
            if q.get("ales_max"):
                where.append("(k.ales_min IS NULL OR k.ales_min <= ?)")
                args.append(float(q["ales_max"]))
            if q.get("min_adet"):
                where.append("k.kadro_sayisi >= ?")
                args.append(int(q["min_adet"]))
        except ValueError:
            pass
        if q.get("aktif") == "1":
            where.append("i.arsivlendi = 0")
        w = ("WHERE " + " AND ".join(where)) if where else ""
        order = {"adet": "k.kadro_sayisi DESC, i.yayin_tarihi DESC", "kurum": "i.kurum COLLATE NOCASE, k.id"}.get(
            q.get("sirala"), "i.yayin_tarihi DESC, k.ilan_id DESC, k.id")
        try:
            page = max(1, int(q.get("sayfa", 1)))
        except ValueError:
            page = 1
        base = "FROM kadrolar k JOIN ilanlar i ON i.id = k.ilan_id " + w
        agg = query(f"SELECT COUNT(*) n, SUM(k.kadro_sayisi) s {base}", args)
        rows = query(
            "SELECT k.id, k.ilan_id, k.pozisyon_no, k.birim, k.bolum, k.anabilim_dali, k.uzmanlik, k.unvan, "
            "k.unvan_grup, k.kadro_sayisi, k.derece, k.ales_puani, k.ales_puan_turu, k.yabanci_dil_puani, "
            "k.ales_min, k.dil_min, k.sartlar, k.aciklama, i.kurum, i.il, i.yayin_tarihi, i.arsivlendi, "
            f"i.baslik ilan_baslik, i.url {base} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [PAGE_SIZE, (page - 1) * PAGE_SIZE])
        return {"toplam": agg[0]["n"] if agg else 0, "toplam_adet": agg[0]["s"] if agg else 0,
                "sayfa": page, "sayfa_boyutu": PAGE_SIZE, "kadrolar": rows}

    def list_bolumler(q):
        where, args = [], []
        if q.get("q"):
            clause, a = match_clause("k.bolum_key", q["q"])
            if clause:
                where.append(clause)
                args += a
        if q.get("unvan"):
            parts = [p for p in q["unvan"].split("|") if p]
            where.append("(" + " OR ".join("k.unvan_grup LIKE ?" for _ in parts) + ")")
            args += [f"%{p}%" for p in parts]
        if q.get("aktif") == "1":
            where.append("i.arsivlendi = 0")
        where.append("k.bolum_key != ''")
        rows = query(
            "SELECT k.bolum_key AS key, MIN(k.bolum_ad) AS ad, COUNT(*) AS satir, SUM(k.kadro_sayisi) AS kadro, "
            "COUNT(DISTINCT i.kurum) AS kurum FROM kadrolar k JOIN ilanlar i ON i.id = k.ilan_id "
            "WHERE " + " AND ".join(where) + " GROUP BY k.bolum_key ORDER BY ad COLLATE NOCASE", args)
        return {"bolumler": rows}

    def sartlar_raporu(body):
        keys = [k for k in body.get("bolumler", []) if isinstance(k, str)][:2000]
        if not keys:
            return {"gruplar": [], "toplam_satir": 0}
        where = [f"k.bolum_key IN ({','.join('?' * len(keys))})"]
        args = list(keys)
        unv = [u for u in body.get("unvan", []) if isinstance(u, str)]
        if unv:
            where.append("(" + " OR ".join("k.unvan_grup LIKE ?" for _ in unv) + ")")
            args += [f"%{u}%" for u in unv]
        if body.get("aktif"):
            where.append("i.arsivlendi = 0")
        rows = query(
            "SELECT k.bolum_key, k.bolum_ad, k.birim, k.anabilim_dali, k.uzmanlik, k.unvan_grup, k.unvan, "
            "k.kadro_sayisi, k.derece, k.ales_min, k.ales_puani, k.ales_puan_turu, k.dil_min, "
            "k.yabanci_dil_puani, k.sartlar, k.aciklama, k.ilan_id, i.kurum, i.il, i.yayin_tarihi, i.url "
            "FROM kadrolar k JOIN ilanlar i ON i.id = k.ilan_id WHERE " + " AND ".join(where) +
            " ORDER BY k.bolum_key, k.unvan_grup, i.kurum COLLATE NOCASE", args)
        groups = {}
        for r in rows:
            g = groups.setdefault(r["bolum_key"], {"key": r["bolum_key"], "ad": r["bolum_ad"], "girdiler": {}})
            sart_txt = " ".join(x for x in (r["sartlar"], r["uzmanlik"], r["aciklama"]) if x)
            mk = (r["unvan_grup"], fold(sart_txt))  # ayni unvan + ayni sart metni -> tek madde, birden cok kurum
            e = g["girdiler"].setdefault(mk, {
                "unvan_grup": r["unvan_grup"] or r["unvan"],
                "uzmanlik": r["uzmanlik"], "sart_maddeleri": sart_maddeleri(r["sartlar"]),
                "aciklama_maddeleri": sart_maddeleri(r["aciklama"]), "kaynaklar": []})
            e["kaynaklar"].append({k: r[k] for k in (
                "kurum", "il", "birim", "anabilim_dali", "kadro_sayisi", "derece", "ales_min", "ales_puani",
                "ales_puan_turu", "dil_min", "yabanci_dil_puani", "ilan_id", "yayin_tarihi", "url")})
        out = []
        for g in sorted(groups.values(), key=lambda x: fold(x["ad"])):
            g["girdiler"] = list(g["girdiler"].values())
            g["kadro_toplam"] = sum(k["kadro_sayisi"] or 0 for e in g["girdiler"] for k in e["kaynaklar"])
            out.append(g)
        return {"gruplar": out, "toplam_satir": len(rows)}

    def build_xlsx(rapor):
        """sartlar_raporu ciktisini iki sayfalik Excel dosyasina (bytes) cevirir."""
        import io
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        def puan(v, ham, tur=None):
            if v is not None:
                return f"{v:g}" + (f" ({tur})" if tur else "")
            return ham or ""

        wb = Workbook()
        ws = wb.active
        ws.title = "Şartlar"
        head = ["Bölüm", "Ünvan", "Kurum", "İl", "Birim", "Anabilim dalı", "Kadro", "Derece", "ALES", "Yabancı dil",
                "Özel şartlar", "Açıklama", "Uzmanlık alanı", "Yayın tarihi", "İlan bağlantısı"]
        ws.append(head)
        for g in rapor["gruplar"]:
            for e in g["girdiler"]:
                madde = "\n".join(f"{i}. {m}" for i, m in enumerate(e["sart_maddeleri"], 1))
                acik = "\n".join(f"{i}. {m}" for i, m in enumerate(e["aciklama_maddeleri"], 1))
                for k in e["kaynaklar"]:
                    ws.append([g["ad"], e["unvan_grup"], k["kurum"], k["il"], k["birim"], k["anabilim_dali"],
                               k["kadro_sayisi"], k["derece"], puan(k["ales_min"], k["ales_puani"], k["ales_puan_turu"]),
                               puan(k["dil_min"], k["yabanci_dil_puani"]), madde or ("" if acik or e["uzmanlik"] else "(tabloda özel şart yok)"), acik,
                               e["uzmanlik"], (k["yayin_tarihi"] or "")[:10], k["url"]])
        fill = PatternFill("solid", fgColor="1D4ED8")
        for c in ws[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), fill
            c.alignment = Alignment(vertical="center", wrap_text=True)
        widths = [28, 18, 30, 12, 28, 24, 8, 8, 14, 14, 70, 40, 30, 12, 40]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        ozet = wb.create_sheet("Bölüm özeti")
        ozet.append(["Bölüm", "Kurum sayısı", "Kadro toplamı", "Farklı şart sayısı"])
        for g in rapor["gruplar"]:
            kurumlar = {k["kurum"] for e in g["girdiler"] for k in e["kaynaklar"]}
            ozet.append([g["ad"], len(kurumlar), g["kadro_toplam"], len(g["girdiler"])])
        for c in ozet[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), fill
        for i, w in enumerate([50, 14, 14, 18], 1):
            ozet.column_dimensions[get_column_letter(i)].width = w
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json; charset=utf-8", filename=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/index.html"):
                return self.send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/durum":
                return self.send(200, scraper.status())
            if u.path == "/api/filtreler":
                col = lambda c: [r["v"] for r in query(
                    f"SELECT DISTINCT {c} AS v FROM ilanlar WHERE {c} IS NOT NULL AND {c}!='' "
                    f"ORDER BY {c} COLLATE NOCASE")]
                stats = query("SELECT COUNT(*) n, COUNT(detay_cekildi) d, MAX(son_gorulme) son FROM ilanlar")
                ug = [r["u"] for r in query(
                    "SELECT unvan_grup u, COUNT(*) n FROM kadrolar WHERE unvan_grup!='' AND unvan_grup NOT LIKE '%,%' "
                    "GROUP BY 1 ORDER BY n DESC")]
                return self.send(200, {"il": col("il"), "kurum": col("kurum"), "unvan_grup": ug,
                                       "kategori": col("alt_kategori"),
                                       "stats": stats[0] if stats else {"n": 0, "d": 0, "son": None}})
            if u.path == "/api/kadrolar":
                return self.send(200, search_kadrolar(q))
            if u.path == "/api/bolumler":
                return self.send(200, list_bolumler(q))
            if u.path == "/api/ilanlar":
                where, args = [], []
                if q.get("q"):
                    like = f"%{q['q']}%"
                    where.append("(baslik LIKE ? OR kurum LIKE ? OR icerik_metin LIKE ? OR ilan_no LIKE ?)")
                    args += [like] * 4
                for key, col in (("il", "il"), ("kurum", "kurum"), ("kategori", "alt_kategori")):
                    if q.get(key):
                        where.append(f"{col} = ?")
                        args.append(q[key])
                if q.get("arsiv") in ("0", "1"):
                    where.append("arsivlendi = ?")
                    args.append(int(q["arsiv"]))
                w = ("WHERE " + " AND ".join(where)) if where else ""
                order = SORTS.get(q.get("sirala"), SORTS["yeni"])
                try:
                    page = max(1, int(q.get("sayfa", 1)))
                except ValueError:
                    page = 1
                total = query(f"SELECT COUNT(*) n FROM ilanlar {w}", args)
                total = total[0]["n"] if total else 0
                rows = query(f"SELECT {LIST_COLS} FROM ilanlar {w} ORDER BY {order} LIMIT ? OFFSET ?",
                             args + [PAGE_SIZE, (page - 1) * PAGE_SIZE])
                return self.send(200, {"toplam": total, "sayfa": page, "sayfa_boyutu": PAGE_SIZE,
                                       "ilanlar": rows})
            if u.path.startswith("/api/ilan/"):
                try:
                    i = int(u.path.rsplit("/", 1)[1])
                except ValueError:
                    return self.send(400, {"hata": "gecersiz id"})
                rows = query("SELECT id, ilan_no, baslik, kurum, kurum_kodu, kaynak, il, ilce, "
                             "yayin_tarihi, resmi_gazete_tarihi, kategori, goruntulenme, arsivlendi, "
                             "url, icerik_html, icerik_metin, detay_cekildi, ilk_gorulme, son_gorulme "
                             "FROM ilanlar WHERE id=?", (i,))
                return self.send(200, rows[0]) if rows else self.send(404, {"hata": "bulunamadi"})
            self.send(404, {"hata": "yok"})

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/cek", "/api/sartlar", "/api/sartlar.xlsx"):
                return self.send(404, {"hata": "yok"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if path == "/api/sartlar":
                return self.send(200, sartlar_raporu(body if isinstance(body, dict) else {}))
            if path == "/api/sartlar.xlsx":
                try:
                    data = build_xlsx(sartlar_raporu(body if isinstance(body, dict) else {}))
                except ImportError:
                    return self.send(501, {"hata": "Excel icin 'pip install openpyxl' gerekli"})
                return self.send(200, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 filename="bolum_sartlari.xlsx")
            ok = scraper.start(bool(body.get("refresh")))
            self.send(200 if ok else 409, {"basladi": ok,
                                           "mesaj": "Cekme basladi" if ok else "Zaten calisiyor"})

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "ilanlar.db"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(a.db, Scraper(a.db)))
    print(f"Arayuz: http://{a.host}:{a.port}  (durdurmak icin Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
