# E-Ticaret Dataset Scraper — Cheatsheet

Trendyol, Mavi, Zara, Hepsiburada ve genel e-ticaret sitelerinden ürün görsellerini standart `.jpg` olarak `images/` klasörüne indirir ve görsel–ürün–özellik eşlemesini `catalog.csv` dosyasına yazar.

---

## 1. Kurulum

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

> Not: Sistemde Google Chrome veya Microsoft Edge kuruluysa scraper bot korumasını (Zara/Mavi) aşmak için otomatik olarak önce onları kullanır.

---

## 2. Parametreler

| Parametre | Kısa | Varsayılan | Açıklama |
|---|---|---|---|
| `--list-url URL` | `-l` | — | Kategori veya arama sayfası URL'si (Google arama linki de olabilir) |
| `--pages N` | `-p` | `20` | Taranacak sayfa sayısı (Zara'da scroll adımı olarak kullanılır) |
| `--max-products N` | `-m` | sınırsız | En fazla çekilecek ürün sayısı |
| `--max-images N` | — | `3` | Ürün başına kaydedilecek (kalite filtresini geçen) görsel sayısı; `0` = sınırsız |
| `--max-side PX` | — | `768` | Uzun kenar bu değeri aşarsa küçültülür (tüm dataset tutarlı boyutta kalır) |
| `--min-side PX` | — | `300` | Kısa kenarı bundan küçük görseller elenir |
| `--dup-threshold N` | — | `4` | dHash Hamming mesafesi ≤ N olan görseller near-duplicate sayılıp atlanır; `-1` = kapalı |
| `--category ETIKET` | — | boş | Çalıştırmadaki tüm ürünlere kategori etiketi yazar (boşsa isimden tahmin edilir) |
| `--file DOSYA` | `-f` | — | Her satırda bir ürün URL'si bulunan metin dosyası |
| `--output DOSYA` | `-o` | `catalog.csv` | Çıktı katalog CSV dosyası |
| `--images-dir KLASOR` | — | `images` | İndirilen `.jpg` görsellerin kaydedileceği klasör |
| `--append` | — | açık (varsayılan) | Mevcut `catalog.csv` üzerine duplike ürünleri atlayarak ekler |
| `--overwrite` | — | kapalı | Mevcut `catalog.csv` dosyasını sıfırlayıp baştan yazar |
| `--browser` | — | kapalı | Tekil ürün URL'lerinde Playwright tarayıcı kullan (`-l` ile otomatik açılır) |
| `--delay SANIYE` | — | `2.0` | İstekler arası bekleme süresi (saniye) |

---

## 3. Hızlı Kullanım Örnekleri

### Çoklu siteden tek bir dataset oluşturma (duplike otomatik engellenir)

```powershell
# 1) Zara'dan başla (ürün başına en fazla 4 görsel)
python scraper.py -l "https://www.zara.com/tr/tr/search?searchTerm=ceket" -p 5 --max-images 4

# 2) Mavi'yi aynı catalog.csv + images/ içine ekle (mevcut katalog otomatik korunur)
python scraper.py -l "https://www.mavi.com/search/?text=ceket" -p 10 --max-images 4

# 3) Trendyol'u aynı dataset'e ekle
python scraper.py -l "https://www.trendyol.com/sr?q=ceket" -p 20 --max-images 4

# 4) Aynı siteyi tekrar çalıştırsanız bile mevcut ürünler atlanır, duplike oluşmaz
python scraper.py -l "https://www.mavi.com/search/?text=ceket" -p 10 --max-images 4
```

### Hızlı deneme (küçük örneklem)

```powershell
python scraper.py -l "https://www.mavi.com/search/?text=jean" -p 1 -m 5 --max-images 3 --delay 1
```

### Tekil ürün linklerini çekme

```powershell
python scraper.py "https://www.mavi.com/siyah-ceket/p/1110703-900" "https://www.zara.com/tr/tr/cift-tarafli-ceket-p06318345.html" --browser
```

### Dosyadan URL listesi çekme

`urls.txt` içine her satıra bir ürün linki yazın (`#` ile başlayan satırlar atlanır):

```powershell
python scraper.py -f urls.txt --browser --max-images 5 -o catalog.csv
```

### Farklı bir klasör / çıktı adı kullanma

```powershell
python scraper.py -l "https://www.trendyol.com/sr?q=ayakkabi" -p 5 --max-images 3 --images-dir dataset_ayakkabi/images -o dataset_ayakkabi/catalog.csv
```

---

## 4. Çıktı Yapısı

### Klasör düzeni
```text
scraper/
├── scraper.py
├── requirements.txt
├── catalog.csv
└── images/
    ├── zara_03e8776011_01.jpg
    ├── zara_03e8776011_02.jpg
    ├── mavi_b5348f1e95_01.jpg
    └── trendyol_6651b5791f_01.jpg
```

### `catalog.csv` sütunları (her satır = 1 görsel)

| Sütun | Açıklama |
|---|---|
| `product_id` | Deterministik ürün kimliği (`<site>_<url_hash>`) |
| `group_id` | Aynı modelin renk varyantlarını birleştiren grup (Mavi: ürün kodu; diğerleri: `product_id`) |
| `split` | `train` / `val` / `test` (80/10/10). `group_id` hash'inden deterministik; aynı grup hep aynı split'te → sızıntı olmaz |
| `site` | Kaynak site (`trendyol`, `mavi`, `zara`, `hepsiburada` veya domain adı) |
| `url` | Ürün sayfası linki |
| `isim` | Ürün başlığı |
| `kategori` | `--category` ile verilen veya isimden tahmin edilen etiket (ceket, mont, pantolon, ...) |
| `marka` | Özelliklerden veya siteden |
| `renk` | Özelliklerdeki ana renk |
| `image_index` | Ürünün kaçıncı görseli (`1`, `2`, `3`, ...) |
| `image_path` | Diskteki standart `.jpg` dosya yolu (`images/...jpg`) |
| `image_url` | Görselin orijinal CDN adresi |
| `width`, `height` | Kaydedilen görselin piksel boyutu |
| `phash` | 64 bit dHash (hex); near-duplicate tespiti için |
| `ozellikler` | Okunabilir metin formatı (`Renk: Siyah; Kesim: Oversize; ...`) |
| `ozellikler_json` | Python/ML tarafında doğrudan `json.loads()` ile okunabilen özellik sözlüğü |

---

## 5. Python'da Dataset Okuma Örneği (Benzerlik / Arama)

```python
import csv
import json
from PIL import Image

with open("catalog.csv", encoding="utf-8-sig") as f:
    catalog = list(csv.DictReader(f))

for row in catalog[:3]:
    img = Image.open(row["image_path"])
    features = json.loads(row["ozellikler_json"])
    print(row["product_id"], img.size, row["isim"], features)
```

---

## 6. İpuçları

- **Görsel benzerlik için görsel kalitesi:** Her görsel indirilirken RGB JPEG'e çevrilir, uzun kenarı `--max-side` ile sınırlanır; çok küçük, bozuk veya garip en-boy oranlı görseller elenir. Aynı görselin tekrarı (örn. farklı satıcıdaki aynı ürün) dHash ile tüm katalog genelinde elenir.
- **Eski `catalog.csv`:** Yeni şemadan eski bir katalog varsa ilk çalıştırmada otomatik yükseltilir (yeni sütunlar eklenir, eski görseller `--max-side`'a küçültülür).
- **Train/val/test:** `split` sütununu kullanın; rastgele satır bazlı bölmeyin (aynı ürünün görselleri iki tarafa düşer).
- **Kategori etiketi:** Tek kategorilik çalıştırmalarda `--category ceket` verin; liste sonuçları gürültülü olabileceği için isimden tahmin edilen etiketi de gözle kontrol edin.
- **Yarıda kesilirse:** `--append` ile aynı komutu yeniden çalıştırın; zaten kataloğa yazılmış ürünler atlanır, çift satır oluşmaz.
- **Rate-limit / engel görürseniz:** `--delay 3` veya `--delay 4` ile bekleme süresini artırın.
