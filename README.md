# Akademik Personel İlanları Toplayıcı

[ilan.gov.tr](https://www.ilan.gov.tr/ilan/kategori/73/akademik-personel-alimlari) üzerindeki **Akademik Personel Alımları** ilanlarını çekip yerel bir SQLite veritabanında toplar, ilanlardaki kadro tablolarını ayrıştırır ve bir web arayüzünden aramanızı sağlar. Seçtiğiniz bölümlerin ilan şartlarını Excel'e aktarabilirsiniz.

> **Kullanım uyarısı:** Bu araç yalnızca kişisel/araştırma amaçlı ve makul sıklıkta kullanım için yazılmıştır. İlan içerikleri ilgili kurumlara aittir; ilan.gov.tr'nin kullanım koşullarına uymak kullanıcının sorumluluğundadır. Araç, sayfaları sıradan bir tarayıcı gibi gezer ve istekler arasında bekler; gereksiz sıklıkta çalıştırmayın.
>
> Çekilen ilanlar (`ilanlar.db`) ve dışa aktarılan dosyalar repoya **dahil değildir** (`.gitignore`). Veriyi kendi makinenizde çekip üretirsiniz.
>
> **Güvenlik:** Web arayüzü kimlik doğrulaması içermez ve yalnızca kendi bilgisayarınızda (`127.0.0.1`) çalışmak üzere tasarlanmıştır. İnternete veya ağa açmayın.

## Hızlı başlangıç

```bash
# 1) Kurulum (bir kez) — Python 3.10+ gerekir
git clone https://github.com/TahsinGokalp/ilan-akademik-personel.git
cd ilan-akademik-personel
pip install -r requirements.txt
python -m playwright install chromium

# 2) Arayüzü başlat
python server.py
```

3. Tarayıcıda <http://127.0.0.1:8000> adresini açın.
4. Sağ üstteki **İlanları çek / güncelle** → **Başlat**. İlk çekme birkaç dakika sürer (≈85 ilan, her biri için ayrı sayfa açılır). Bitince liste kendiliğinden dolar.

Sonraki günlerde aynı düğmeye basmanız yeterli: yeni ilanlar eklenir, eskiler silinmez.

## Arayüz

Üç sekme vardır.

### Kadrolar
İlan tablolarından çıkarılan her kadro satırı için arama.
- **Birim/bölüm/kurum kutusu:** virgül = VEYA, boşluk = VE. Örn. `işletme, pazarlama` + ünvan "Öğretim Görevlisi" → işletme veya pazarlama bölümlerindeki öğretim görevlisi kadroları. Türkçe harfler (İ/ı, ş, ğ…) fark etmez.
- **Şartlar kutusu:** ilan şartı/açıklama metninde arar (aynı VE/VEYA kuralı).
- Ünvan, il, kurum, "ALES en çok", "kadro en az", yalnızca aktif ilanlar filtreleri.

### Bölüm şartları
Bölümleri listeden toplu seçip özel şartlarını görmek ve dışa aktarmak için.
1. Listeyi filtreleyin (`işletme, pazarlama`), bölümleri tek tek işaretleyin veya **Görünenlerin hepsini seç** deyin. Filtreyi değiştirseniz de seçim korunur.
2. İsterseniz ünvan ve "sadece aktif ilanlar" ile daraltın.
3. Altta her bölüm için ünvana göre şart kartları çıkar: şartlar madde madde, açıklama ayrı, kaynak olarak kurum · birim · kadro · derece · ALES/dil puanı · ilan bağlantısı. Aynı ünvan ve aynı şart metni birden çok kurumda varsa tek kartta birleşir.
4. Dışa aktarma:
   - **Excel'e aktar (.xlsx):** "Şartlar" sayfası (kurum başına bir satır, filtrelenebilir, şartlar numaralı maddeler hâlinde) ve "Bölüm özeti" sayfası.
   - **Markdown olarak kopyala**, **CSV indir**, **Yazdır**.

### İlanlar
Tüm ilanlar; başlık, kurum, ilan metni ve ilan numarasında arama; il/kurum/kategori/aktif-arşiv filtreleri. Bir ilana tıklayınca ilan metni orijinal tablolarıyla açılır ("Düz metin göster" ile metin görünümü).

## Komut satırı

```bash
python ilan_cek.py              # yeni ilanları ekler, detayı olanları atlar
python ilan_cek.py --refresh    # tüm detayları yeniden çeker
python ilan_cek.py --headed     # tarayıcıyı görünür açar (hata ayıklama)
python ilan_cek.py --delay 1.5  # detay istekleri arası bekleme (sn, varsayılan 0.7)
python ilan_cek.py --db x.db    # farklı veritabanı dosyası

python parse_kadro.py --rapor   # kadro tablolarını yeniden ayrıştır + özet rapor
python server.py --port 9000    # farklı port (varsayılan 8000)
```

Sunucu varsayılan olarak yalnızca yerel makineden erişilebilir ve kimlik doğrulaması yoktur; `--host 0.0.0.0` ile ağa **açmayın**.

## Dosyalar

| Dosya | Görevi |
|---|---|
| `ilan_cek.py` | İlanları ve detaylarını çeker, `ilanlar.db`'ye yazar; sonunda kadro ayrıştırmayı çalıştırır |
| `parse_kadro.py` | İlan tablolarından kadro satırlarını (`kadrolar` tablosu) çıkarır, şartları maddelere ayırır |
| `server.py` | Web arayüzü + API (yalnızca standart kütüphane; Excel için `openpyxl`) |
| `index.html` | Arayüz |
| `requirements.txt` | Python bağımlılıkları |
| `ilanlar.db` | Veritabanı (ilk çekmede oluşur, git'e girmez) |

## Nasıl çalışır?

Site bir Angular uygulaması: sayfa açılırken ilan verisini kendi servisinden alıp ekranda gösteriyor. Script, sayfaları bir tarayıcıda (Playwright/Chromium) sıradan bir ziyaretçi gibi açar ve sayfanın yüklediği ilan verisini okur:

1. Liste sayfaları (`?currentPage=1,2,…`) boş sayfaya gelene kadar gezilir. (Site `0` ve `1` için aynı ilk sayfayı döndürür.)
2. Her ilanın sayfası açılıp detay yanıtı (ilan metni dahil) kaydedilir.
3. İlan HTML'indeki tablolar ayrıştırılıp `kadrolar` tablosuna yazılır.

### Artımlı güncelleme
- Hiçbir ilan **silinmez**; siteden kalkanlar veritabanında kalır (`son_gorulme` eskiyse siteden kalkmış olabilir).
- Yeni ilanlar eklenir, mevcutların liste bilgileri güncellenir, detayı çekilmiş olanlar atlanır; hata verenler sonraki çalıştırmada tekrar denenir.
- `kadrolar` türetilmiş veridir; her çalıştırmada `ilanlar`'dan baştan üretilir.

## Veritabanı

**`ilanlar`**: `id`, `ilan_no`, `baslik`, `kurum`, `il`, `ilce`, `yayin_tarihi`, `resmi_gazete_tarihi`, `kategori`, `alt_kategori`, `goruntulenme`, `arsivlendi`, `url`, `icerik_html`, `icerik_metin`, ham JSON'lar (`liste_json`, `detay_json`), `detay_cekildi`, `ilk_gorulme`, `son_gorulme`.

**`kadrolar`** (ilan tablolarından): `ilan_id`, `birim`, `bolum`, `anabilim_dali`, `unvan`, `unvan_grup` (Profesör, Doçent, Dr. Öğr. Üyesi, Öğretim Görevlisi, Araştırma Görevlisi…), `kadro_sayisi`, `derece`, `ales_puani`, `ales_min`, `yabanci_dil_puani`, `dil_min`, `sartlar`, `aciklama`, `uzmanlik`, `bolum_ad`/`bolum_key` (bölüm adı ve normalleştirilmiş anahtarı), `ara`/`ara_sart` (Türkçe karakterleri sadeleştirilmiş arama alanları), `ham_json`.

```sql
SELECT i.kurum, k.birim, k.bolum, k.kadro_sayisi, k.ales_min, k.sartlar
FROM kadrolar k JOIN ilanlar i ON i.id = k.ilan_id
WHERE k.unvan_grup = 'Öğretim Görevlisi' AND (k.ara LIKE '%isletme%' OR k.ara LIKE '%pazarlama%');
```

## Bilinen sınırlar

- Tablo başlıkları üniversiteden üniversiteye değişir; başlıklar eş anlamlı sözcüklerle eşlenir. Yeni bir düzen yanlış ayrışırsa `python parse_kadro.py --rapor` ile kontrol edin; ham satır `ham_json`'da saklanır.
- Kadro bilgisi tabloda değil düz metinde yazılmış ilanlar ayrıştırılamaz.
- Kadroların bir kısmında tabloda şart sütunu yoktur; genel şartlar ilan metnindedir (arayüzde "ilan" bağlantısı).
- `ales_min`/`dil_min` tabloda sütun varsa oradan, yoksa şart metninden ("en az 70 puan") çıkarılır; boşsa "puan şartı yok" anlamına gelmeyebilir.
- Sitenin arayüzü/API'si değişirse `ilan_cek.py` güncelleme gerektirebilir (liste yanıtı `Ad/AdsByFilter`, detay `AdDetail/GetAdDetail`).

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `playwright` / Chromium bulunamadı | `pip install -r requirements.txt` ve `python -m playwright install chromium` |
| Excel düğmesi "openpyxl gerekli" der | `pip install openpyxl` ve sunucuyu yeniden başlatın |
| Liste boş | Önce **İlanları çek / güncelle** ile veriyi çekin |
| Port kullanımda | `python server.py --port 9000` |
| Çekme takılıyor/zaman aşımı | `--headed` ile izleyin; ağ bağlantısını ve siteye erişimi kontrol edin |

## Sorumluluk ve lisans

Araya bekleme konarak nazik davranılır; gereksiz sıklıkta çalıştırmayın. Sitenin kullanım koşullarına uymak sizin sorumluluğunuzdadır. İlan içerikleri ilgili kurumlara aittir; bu yüzden veritabanı ve dışa aktarımlar repoya eklenmez. Bu proje ilan.gov.tr veya Basın İlan Kurumu ile bağlantılı değildir.

Yazılım olduğu gibi, herhangi bir garanti verilmeden sunulur. Lisans için bkz. [LICENSE](LICENSE).
