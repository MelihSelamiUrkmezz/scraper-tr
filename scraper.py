"""Esnek E-Ticaret Dataset Scraper -> images/ + catalog.csv
(Trendyol, Mavi, Zara, Hepsiburada + Genel)

Kullanım:
    python scraper.py -l "https://www.mavi.com/search/?text=ceket" -p 5
    python scraper.py -l "https://www.zara.com/tr/tr/search?searchTerm=ceket" -p 3 --append
    python scraper.py -l "https://www.trendyol.com/sr?q=ceket" -p 20 --max-images 5 --append
    python scraper.py URL1 URL2 --browser -o catalog.csv --images-dir images

Kurulum:
    pip install requests beautifulsoup4 playwright
    python -m playwright install chromium
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_requests(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


_pw = {}


def _ensure_browser():
    if "page" in _pw:
        return _pw["page"]
    from playwright.sync_api import sync_playwright

    _pw["p"] = sync_playwright().start()
    launch_args = ["--disable-blink-features=AutomationControlled"]
    browser = None
    for ch in ("chrome", "msedge", None):
        try:
            kw = {"headless": True, "args": launch_args}
            if ch:
                kw["channel"] = ch
            browser = _pw["p"].chromium.launch(**kw)
            break
        except Exception:
            continue
    if browser is None:
        raise RuntimeError("Playwright tarayıcısı başlatılamadı")
    ctx = browser.new_context(
        locale="tr-TR",
        viewport={"width": 1366, "height": 768},
        user_agent=HEADERS["User-Agent"],
    )
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    _pw["b"] = browser
    _pw["page"] = ctx.new_page()
    return _pw["page"]


def fetch_browser(url, scroll_steps=1):
    # Tek bir tarayıcı oturumu tüm istekler için yeniden kullanılır
    page = _ensure_browser()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    for _ in range(scroll_steps):
        page.mouse.wheel(0, 12000)
        page.wait_for_timeout(1200)
    return page.content()


def close_browser():
    if "b" in _pw:
        try:
            _pw["b"].close()
        except Exception:
            pass
    if "p" in _pw:
        try:
            _pw["p"].stop()
        except Exception:
            pass
    _pw.clear()


def _abs_url(base, u):
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    return urljoin(base, u)


def json_ld_product(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = list(data) if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            if "@graph" in item and isinstance(item["@graph"], list):
                stack.extend(item["@graph"])
            t = item.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(x in ("Product", "ProductGroup") for x in types):
                return item
    return {}


def as_list(v):
    if not v:
        return []
    if isinstance(v, dict):
        v = v.get("contentUrl") or v.get("url") or []
    return v if isinstance(v, list) else [v]


def _ld_images(ld, base_url):
    out = []
    for i in as_list(ld.get("image")):
        u = i if isinstance(i, str) else (i.get("contentUrl") or i.get("url") or "")
        if isinstance(u, list):
            out.extend(_abs_url(base_url, x) for x in u if isinstance(x, str))
        elif u:
            out.append(_abs_url(base_url, u))
    return out


def detect_site(url):
    host = urlparse(url).netloc.lower()
    for name in ("trendyol", "hepsiburada", "mavi", "zara"):
        if name in host:
            return name
    return host.replace("www.", "") or "generic"


# ---------------- Site Bazlı Ürün Parser'ları ----------------

def extract_trendyol_attributes(html):
    dec = json.JSONDecoder()
    for m in re.finditer(r'"attributes":\[\{"key"', html):
        try:
            arr, _ = dec.raw_decode(html, m.start() + len('"attributes":'))
        except json.JSONDecodeError:
            continue
        return arr
    return []


def parse_trendyol(url, html, soup):
    ld = json_ld_product(soup)
    name = ld.get("name", "")
    if not name and soup.find("h1"):
        name = soup.find("h1").get_text(" ", strip=True)
    images = _ld_images(ld, url)
    if not images:
        images = re.findall(r"https://cdn\.dsmcdn\.com/[^\"\s]*?/prod/[^\"\s]*?_org_zoom\.jpg", html)
    feats = {}
    for a in extract_trendyol_attributes(html):
        k = (a.get("key") or {}).get("name")
        v = (a.get("value") or {}).get("name")
        if k and v and v != "Belirtilmemiş":
            feats[k] = v
    brand = ld.get("brand")
    brand_name = brand.get("name") if isinstance(brand, dict) else brand
    if brand_name and isinstance(brand_name, str) and "Marka" not in feats:
        feats["Marka"] = brand_name
    return name, images, feats


def parse_hepsiburada(url, html, soup):
    ld = json_ld_product(soup)
    name = ld.get("name") or (soup.find("h1").get_text(strip=True) if soup.find("h1") else "")
    images = _ld_images(ld, url)
    if not images:
        og = soup.find("meta", property="og:image")
        images = [_abs_url(url, og["content"])] if og and og.get("content") else []
    feats = {}
    for row in soup.select("[data-test-id='product-features'] tr, #productTechSpecContainer tr, table.data-list tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) >= 2:
            feats[cells[0]] = cells[1]
    if not feats:
        for k, v in zip(soup.select(".techSpecItem .techSpecKey, [class*='spec'] [class*='key']"),
                        soup.select(".techSpecItem .techSpecValue, [class*='spec'] [class*='value']")):
            feats[k.get_text(strip=True)] = v.get_text(strip=True)
    return name, images, feats


MAVI_NOISE_TITLES = (
    "yıkama", "bakım", "iade", "teslimat", "kargo", "ödeme", "mağaza",
    "üretici", "imalatçı", "güvenlik", "manken",
)
MAVI_NOISE_TEXT = (
    "ürün kodu:", "üretim yeri:", "yerleşik imalatçı", "posta adresi:",
    "e-posta", "fiyat geçerlilik", "teslimat ve kargo", "iade ve değişim",
    "mağazadan iade", "geri ödeme", "hijyen ve ürün bazlı", "mavi giyim sanayi",
)


def parse_mavi(url, html, soup):
    name = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else ""
    if not name:
        og = soup.find("meta", property="og:title")
        name = og["content"].strip() if og and og.get("content") else ""

    # Ürün kodunu URL'den çıkar (/p/1110703-900) ve yüksek çözünürlüklü ürün görsellerini al
    m_code = re.search(r"/p/([A-Za-z0-9-]+)", url)
    code = m_code.group(1) if m_code else ""
    images = []
    if code:
        pat = rf"(?:https?:)?//sky-static\.mavi\.com/[^\s\"'<>]*{re.escape(code)}_image_\d+\.(?:jpg|jpeg|png|webp)[^\s\"'<>]*"
        raw_imgs = re.findall(pat, html, re.I)
        by_idx = {}
        for img in raw_imgs:
            full = _abs_url(url, img.split("?")[0])
            im = re.search(r"_image_(\d+)", full)
            idx = im.group(1) if im else full
            if idx not in by_idx or "/720/" in full or "/1200/" in full:
                by_idx[idx] = full
        images = list(by_idx.values())
    if not images:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            images = [_abs_url(url, og["content"])]

    feats = {}
    fit_el = soup.select_one(".product-details-info-fit")
    if fit_el:
        feats["Kesim"] = fit_el.get_text(" ", strip=True)

    # Renk bilgisini ürün bilgi bloğundan al
    info_el = soup.select_one(".product-details-info")
    if info_el:
        m_color = re.search(r"Renk:\s*([^\n|]+)", info_el.get_text("\n", strip=True))
        if m_color:
            color = m_color.group(1).strip()
            if color and len(color) < 40:
                feats["Renk"] = color

    for sec in soup.select(".product-details-feature-section"):
        title_el = sec.select_one(".product-details-feature-section-title")
        title = title_el.get_text(" ", strip=True) if title_el else "Açıklama"
        if any(nw in title.lower() for nw in MAVI_NOISE_TITLES):
            continue
        items = []
        for x in sec.select(".product-details-feature-fit-list-item, .product-details-feature-text"):
            txt = re.sub(r"\s+", " ", x.get_text(" ", strip=True))
            if not txt or txt == title:
                continue
            if any(nw in txt.lower() for nw in MAVI_NOISE_TEXT):
                continue
            items.append(txt)
        if items:
            val = ", ".join(dict.fromkeys(items))
            if title == "Kumaş Bilgileri":
                val = re.sub(r"^Kumaş:\s*", "", val, flags=re.I)
            if title in feats:
                feats[title] += " | " + val
            else:
                feats[title] = val
    return name, images, feats


def parse_zara(url, html, soup):
    ld = json_ld_product(soup)
    name = ld.get("name") or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
    images = _ld_images(ld, url)
    if not images:
        for img in soup.select("img[src*='static.zara.net']"):
            src = img.get("src") or ""
            if src and "transparent-background" not in src:
                images.append(_abs_url(url, src))

    feats = {}
    brand = ld.get("brand")
    if isinstance(brand, dict) and brand.get("name"):
        feats["Marka"] = brand["name"]
    desc = (ld.get("description") or "").strip()
    if desc:
        feats["Açıklama"] = re.sub(r"\s+", " ", desc)
    variants = as_list(ld.get("hasVariant"))
    colors = list(dict.fromkeys(v.get("color") for v in variants if isinstance(v, dict) and v.get("color")))
    sizes = list(dict.fromkeys(v.get("size") for v in variants if isinstance(v, dict) and v.get("size")))
    if colors:
        feats["Renk"] = ", ".join(colors)
    if sizes:
        feats["Bedenler"] = ", ".join(sizes)
    if not feats:
        og_d = soup.find("meta", property="og:description")
        if og_d and og_d.get("content"):
            feats["Açıklama"] = og_d["content"].strip()
    return name, images, feats


def parse_generic(url, html, soup):
    """Bilinmeyen herhangi bir e-ticaret sitesi için genel JSON-LD + OpenGraph + DOM parser."""
    ld = json_ld_product(soup)
    name = ld.get("name", "")
    if not name:
        h1 = soup.find("h1")
        name = h1.get_text(" ", strip=True) if h1 else ""
    if not name:
        og = soup.find("meta", property="og:title")
        name = og["content"].strip() if og and og.get("content") else ""

    images = _ld_images(ld, url)
    if not images:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            images.append(_abs_url(url, og_img["content"]))

    feats = {}
    brand = ld.get("brand")
    if isinstance(brand, dict) and brand.get("name"):
        feats["Marka"] = brand["name"]
    elif isinstance(brand, str) and brand.strip():
        feats["Marka"] = brand.strip()
    if ld.get("color"):
        feats["Renk"] = str(ld["color"])
    if ld.get("material"):
        feats["Materyal"] = str(ld["material"])
    if ld.get("description"):
        feats["Açıklama"] = re.sub(r"\s+", " ", str(ld["description"]).strip())

    # Tablo (tr > th/td) ve definition list (dl > dt/dd) özelliklerini topla
    for row in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) == 2 and cells[0] and cells[1] and len(cells[0]) < 60:
            feats.setdefault(cells[0], cells[1])
    for dt, dd in zip(soup.select("dl dt"), soup.select("dl dd")):
        k, v = dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)
        if k and v and len(k) < 60:
            feats.setdefault(k, v)
    if not feats:
        og_d = soup.find("meta", property="og:description")
        if og_d and og_d.get("content"):
            feats["Açıklama"] = og_d["content"].strip()
    return name, images, feats


PARSERS = {
    "trendyol": parse_trendyol,
    "hepsiburada": parse_hepsiburada,
    "mavi": parse_mavi,
    "zara": parse_zara,
}


def canonical_product_url(url):
    """Ürün URL'sini tekilleştirme için kanonik hale getirir (şema, fragment, gereksiz query temizliği)."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower()
    path = p.path.rstrip("/")
    # Bilinen e-ticaret sitelerinde ürün kimliği path içindedir; query parametreleri (merchantId, utm vb.) duplike yaratır
    if any(s in host for s in ("trendyol.com", "mavi.com", "zara.com", "hepsiburada.com")):
        return f"{p.scheme.lower()}://{host}{path}"
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme.lower()}://{host}{path}{q}"


def scrape(url, fetch):
    url = canonical_product_url(url)
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    site = detect_site(url)
    parser = PARSERS.get(site, parse_generic)
    name, images, feats = parser(url, html, soup)
    unique_images = list(dict.fromkeys(i for i in images if i))
    return {
        "product_id": make_product_id(site, url),
        "site": site,
        "url": url,
        "isim": name,
        "images": unique_images,
        "feats": feats,
        "ozellikler": "; ".join(f"{k}: {v}" for k, v in feats.items()),
        "ozellikler_json": json.dumps(feats, ensure_ascii=False),
    }


def make_product_id(site, url):
    canon = canonical_product_url(url)
    digest = hashlib.md5(canon.encode("utf-8")).hexdigest()[:10]
    safe_site = re.sub(r"[^a-z0-9]+", "_", site.lower()).strip("_") or "site"
    return f"{safe_site}_{digest}"


def _download_bytes(img_url, referer):
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "image/jpeg,image/png,*/*;q=0.8",
        "Referer": referer,
    }
    try:
        r = requests.get(img_url, headers=headers, timeout=25)
        if r.ok and r.content and len(r.content) > 500:
            return r.content
    except Exception:
        pass
    # CDN requests'i engellerse aktif Playwright oturumu üzerinden indir
    if "page" in _pw:
        resp = _pw["page"].context.request.get(img_url, headers=headers, timeout=25000)
        if resp.ok:
            body = resp.body()
            if body and len(body) > 500:
                return body
    return None


def prepare_image(raw_bytes, max_side, min_side):
    """Ham görseli RGB'ye çevirir, kalite filtresinden geçirir ve uzun kenarı max_side ile sınırlar.
    Döndürür: (PIL.Image | None, ret_nedeni)."""
    try:
        with Image.open(BytesIO(raw_bytes)) as src:
            img = ImageOps.exif_transpose(src)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                img = Image.new("RGB", rgba.size, (255, 255, 255))
                img.paste(rgba, mask=rgba.split()[-1])
            else:
                img = img.convert("RGB")
    except Exception:
        return None, "bozuk görsel"
    w, h = img.size
    if min(w, h) < min_side:
        return None, f"çok küçük ({w}x{h})"
    if not 0.6 <= h / w <= 2.2:
        return None, f"uygunsuz en-boy oranı ({w}x{h})"
    if max(w, h) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img, ""


def save_jpg(img, dest_path):
    img.save(dest_path, format="JPEG", quality=92, optimize=True)


def dhash(img, size=8):
    """64 bitlik difference hash (hex, 16 karakter). Near-duplicate görselleri yakalamak için."""
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = g.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
    return f"{bits:016x}"


def is_near_duplicate(hash_hex, seen_hashes, threshold):
    h = int(hash_hex, 16)
    return any(bin(h ^ s).count("1") <= threshold for s in seen_hashes)


TR_FOLD = str.maketrans({"İ": "i", "I": "ı"})
CATEGORY_KEYWORDS = (
    ("trençkot", "trençkot"), ("trench", "trençkot"), ("yelek", "yelek"),
    ("mont", "mont"), ("kaban", "kaban"), ("palto", "palto"), ("parka", "mont"),
    ("blazer", "ceket"), ("ceket", "ceket"), ("hırka", "hırka"), ("kazak", "kazak"),
    ("sweatshirt", "sweatshirt"), ("tişört", "tişört"), ("t-shirt", "tişört"),
    ("gömlek", "gömlek"), ("bluz", "bluz"), ("elbise", "elbise"), ("etek", "etek"),
    ("pantolon", "pantolon"), ("jean", "pantolon"), ("şort", "şort"),
    ("ayakkabı", "ayakkabı"), ("çanta", "çanta"),
)


def infer_category(name):
    n = (name or "").translate(TR_FOLD).lower()
    for kw, cat in CATEGORY_KEYWORDS:
        if kw in n:
            return cat
    return ""


def make_group_id(site, url, product_id):
    """Aynı modelin renk varyantlarını aynı gruba koyar (train/test sızıntısını önlemek için)."""
    if site == "mavi":
        m = re.search(r"/p/([A-Za-z0-9]+)", url)
        if m:
            return f"mavi_{m.group(1)}"
    return product_id


def assign_split(group_id):
    v = int(hashlib.md5(group_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if v < 80 else "val" if v < 90 else "test"


def enrich_row(row, category=""):
    """Ürün düzeyi sütunlarını (kategori, renk, marka, group_id, split) doldurur; dolu olanları bozmaz."""
    try:
        feats = json.loads(row.get("ozellikler_json") or "{}")
    except json.JSONDecodeError:
        feats = {}
    if not row.get("kategori"):
        row["kategori"] = category or infer_category(row.get("isim", ""))
    if not row.get("renk"):
        color = str(feats.get("Renk", ""))
        row["renk"] = re.split(r"[,/|]", color)[0].strip()
    if not row.get("marka"):
        row["marka"] = feats.get("Marka") or {"mavi": "Mavi", "zara": "Zara"}.get(row.get("site", ""), "")
    if not row.get("group_id"):
        row["group_id"] = make_group_id(row.get("site", ""), row.get("url", ""), row.get("product_id", ""))
    if not row.get("split"):
        row["split"] = assign_split(row["group_id"])
    return row


def download_product_images(product, images_dir, max_images=3, max_side=768, min_side=300,
                            seen_hashes=None, dup_threshold=4, category=""):
    """Ürün görsellerini kalite filtresinden ve near-duplicate kontrolünden geçirip images_dir altına
    standart JPEG olarak kaydeder. max_images kadar geçerli görsel toplanana dek adayları dener."""
    img_dir = Path(images_dir)
    img_dir.mkdir(parents=True, exist_ok=True)
    seen_hashes = seen_hashes if seen_hashes is not None else set()
    rows = []
    for img_url in product["images"]:
        if max_images and len(rows) >= max_images:
            break
        data = _download_bytes(img_url, product["url"])
        if not data:
            continue
        img, reason = prepare_image(data, max_side, min_side)
        if img is None:
            print(f"     ele: {reason} - {img_url[-60:]}", flush=True)
            continue
        h = dhash(img)
        if is_near_duplicate(h, seen_hashes, dup_threshold):
            print(f"     ele: near-duplicate - {img_url[-60:]}", flush=True)
            continue
        idx = len(rows) + 1
        dest = img_dir / f"{product['product_id']}_{idx:02d}.jpg"
        save_jpg(img, dest)
        seen_hashes.add(int(h, 16))
        rows.append(enrich_row({
            "product_id": product["product_id"],
            "site": product["site"],
            "url": product["url"],
            "isim": product["isim"],
            "image_index": idx,
            "image_path": dest.as_posix(),
            "image_url": img_url,
            "width": img.size[0],
            "height": img.size[1],
            "phash": h,
            "ozellikler": product["ozellikler"],
            "ozellikler_json": product["ozellikler_json"],
        }, category=category))
    return rows


def upgrade_catalog(path, max_side):
    """Eski şemalı catalog.csv'yi yeni şemaya taşır: eksik sütunları doldurur, görselleri max_side'a
    küçültür, width/height/phash hesaplar. Döndürür: satır listesi (dosya gerekiyorsa yeniden yazılır)."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)
    changed = header != CATALOG_FIELDS
    for row in rows:
        p = Path(row.get("image_path", ""))
        if not row.get("phash") and p.is_file():
            try:
                with Image.open(p) as im:
                    im.load()
                    img = im.convert("RGB")
                if max(img.size) > max_side:
                    img.thumbnail((max_side, max_side), Image.LANCZOS)
                    save_jpg(img, p)
                row["width"], row["height"] = img.size
                row["phash"] = dhash(img)
                changed = True
            except Exception:
                pass
        before = dict(row)
        enrich_row(row)
        changed = changed or row != before
    if changed:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=CATALOG_FIELDS, extrasaction="ignore", restval="")
            w.writeheader()
            w.writerows(rows)
        print(f"Katalog yeni şemaya yükseltildi: {path} ({len(rows)} satır)", flush=True)
    return rows


# ---------------- Site Bazlı Liste / Sayfalama Toplayıcı ----------------

def normalize_list_url(url):
    """Şema yoksa ekler; Google arama linklerini ilgili sitenin arama sayfasına çevirir."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if "google." in parsed.netloc and parsed.path.startswith("/search"):
        raw_q = (parse_qs(parsed.query).get("q") or [""])[0]
        q_low = raw_q.lower()
        site_templates = [
            ("trendyol", "https://www.trendyol.com/sr?q={q}"),
            ("mavi", "https://www.mavi.com/search/?text={q}"),
            ("zara", "https://www.zara.com/tr/tr/search?searchTerm={q}"),
            ("hepsiburada", "https://www.hepsiburada.com/ara?q={q}"),
        ]
        for kw, tmpl in site_templates:
            if re.search(rf"\b{kw}\b", q_low):
                cleaned = re.sub(rf"\b{kw}\b", "", raw_q, flags=re.I).strip()
                if cleaned:
                    return tmpl.format(q=quote_plus(cleaned))
    return url


def with_query_param(url, key, val):
    url = re.sub(rf"([?&]){re.escape(key)}=\d+&?", r"\1", url).rstrip("?&")
    return url + ("&" if "?" in url else "?") + f"{key}={val}"


def extract_list_links(site, base_url, html):
    soup = BeautifulSoup(html, "html.parser")
    origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    links = []

    if site == "trendyol":
        cards = soup.select("a.product-card[href*='-p-']")
        for a in cards:
            m = re.match(r"(/[^?#]*-p-\d+)", a.get("href", ""))
            if m:
                links.append(origin + m.group(1))
        if not links:
            links = [origin + h for h in re.findall(r'href="(/[^"?#]*-p-\d+)[^"]*"', html)]
    elif site == "mavi":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.match(r"^(https?://[^?#]+/p/[A-Za-z0-9-]+|/[^?#]+/p/[A-Za-z0-9-]+)", href)
            if m:
                links.append(_abs_url(origin, m.group(1)))
    elif site == "zara":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"-p\d+\.html", href):
                links.append(_abs_url(origin, href.split("?")[0]))
    elif site == "hepsiburada":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"-p[m]?-[A-Za-z0-9]+", href):
                links.append(_abs_url(origin, href.split("?")[0]))
    else:
        # Genel e-ticaret link tespiti
        host = urlparse(base_url).netloc
        for a in soup.find_all("a", href=True):
            full = _abs_url(base_url, a["href"]).split("#")[0]
            p = urlparse(full)
            if p.netloc == host and re.search(r"(/p/|/product/|/urun/|-p-\d+|-p\d+\.html)", p.path, re.I):
                links.append(full.split("?")[0])

    return list(dict.fromkeys(links))


def collect_links(list_url, pages, fetch, delay):
    list_url = normalize_list_url(list_url)
    site = detect_site(list_url)
    print(f"Site: {site} | Liste URL: {list_url}", flush=True)

    # Zara infinite-scroll kullanır: sayfa sayısını kaydırma adımına çevir
    if site == "zara":
        html = fetch_browser(list_url, scroll_steps=max(1, pages * 2))
        links = extract_list_links(site, list_url, html)
        print(f"Zara (kaydırma x{max(1, pages * 2)}): {len(links)} ürün bulundu", flush=True)
        return links

    page_param = {"trendyol": "pi", "hepsiburada": "sayfa", "mavi": "page"}.get(site, "page")
    links = []
    seen = set()
    for n in range(1, pages + 1):
        page_url = list_url if n == 1 and site not in ("trendyol",) else with_query_param(list_url, page_param, n)
        try:
            html = fetch(page_url)
        except Exception as e:
            print(f"Sayfa {n} alınamadı: {e}", file=sys.stderr, flush=True)
            break
        found = extract_list_links(site, list_url, html)
        new = [u for u in found if u not in seen]
        seen.update(new)
        print(f"Sayfa {n}: {len(new)} yeni ürün", flush=True)
        if not new:
            break
        links += new
        time.sleep(delay)
    return links


CATALOG_FIELDS = [
    "product_id",
    "group_id",
    "split",
    "site",
    "url",
    "isim",
    "kategori",
    "marka",
    "renk",
    "image_index",
    "image_path",
    "image_url",
    "width",
    "height",
    "phash",
    "ozellikler",
    "ozellikler_json",
]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("-l", "--list-url", help="Kategori/arama sayfası linki (Trendyol, Mavi, Zara, Hepsiburada veya genel)")
    ap.add_argument("-p", "--pages", type=int, default=20, help="Taranacak sayfa sayısı")
    ap.add_argument("-m", "--max-products", type=int, default=None, help="En fazla çekilecek ürün sayısı")
    ap.add_argument("--max-images", type=int, default=3, help="Ürün başına en fazla kaydedilecek (kalite filtresini geçen) görsel sayısı; 0 = sınırsız (varsayılan: 3)")
    ap.add_argument("--max-side", type=int, default=768, help="Görselin uzun kenarı bu değeri aşarsa küçültülür (varsayılan: 768)")
    ap.add_argument("--min-side", type=int, default=300, help="Kısa kenarı bundan küçük görseller elenir (varsayılan: 300)")
    ap.add_argument("--dup-threshold", type=int, default=4, help="dHash Hamming mesafesi bu değerin altındaki görseller near-duplicate sayılır; -1 = kapalı (varsayılan: 4)")
    ap.add_argument("--category", default="", help="Bu çalıştırmadaki tüm ürünlere yazılacak kategori etiketi (boşsa üründen tahmin edilir)")
    ap.add_argument("-f", "--file", help="Her satırda bir URL olan dosya")
    ap.add_argument("-o", "--output", default="catalog.csv", help="Çıktı katalog CSV yolu (varsayılan: catalog.csv)")
    ap.add_argument("--images-dir", default="images", help="Fotoğrafların indirileceği klasör (varsayılan: images)")
    ap.add_argument("--append", action="store_true", help="Mevcut catalog.csv üzerine ekle (varsayılan olarak zaten aktiftir)")
    ap.add_argument("--overwrite", action="store_true", help="Mevcut catalog.csv dosyasını sıfırlayıp baştan yaz")
    ap.add_argument("--browser", action="store_true", help="Playwright ile çek")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            urls += [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    fetch = fetch_browser if (args.browser or args.list_url) else fetch_requests
    try:
        if args.list_url:
            urls += collect_links(args.list_url, args.pages, fetch, args.delay)
        if not urls:
            ap.error("URL veya --list-url verin")

        # Aynı çalıştırma içindeki tekrar eden URL'leri kanonik formda tekilleştir
        deduped_urls = []
        seen_in_run = set()
        for raw_u in urls:
            cu = canonical_product_url(raw_u)
            if cu not in seen_in_run:
                seen_in_run.add(cu)
                deduped_urls.append(cu)

        out_path = Path(args.output)
        append_mode = (not args.overwrite) and out_path.exists() and out_path.stat().st_size > 0
        existing_product_ids = set()
        existing_urls = set()
        existing_pairs = set()
        seen_hashes = set()
        if append_mode:
            for r in upgrade_catalog(out_path, args.max_side):
                pid = r.get("product_id", "")
                if pid:
                    existing_product_ids.add(pid)
                u_old = r.get("url", "")
                if u_old:
                    existing_urls.add(canonical_product_url(u_old))
                existing_pairs.add((pid, r.get("image_path", "")))
                if r.get("image_url"):
                    existing_pairs.add((pid, r.get("image_url", "")))
                if r.get("phash"):
                    seen_hashes.add(int(r["phash"], 16))

        # Katalogda zaten bulunan ürünleri hiç tekrar istek atmadan atla
        new_urls = []
        skipped_count = 0
        for cu in deduped_urls:
            pid = make_product_id(detect_site(cu), cu)
            if pid in existing_product_ids or cu in existing_urls:
                skipped_count += 1
                continue
            new_urls.append(cu)

        if skipped_count:
            print(f"ATLA {skipped_count} ürün zaten {out_path} içinde mevcut (duplike engellendi)", flush=True)

        if args.max_products is not None:
            new_urls = new_urls[: args.max_products]

        prod_count = 0
        img_count = 0
        mode = "a" if append_mode else "w"
        with open(out_path, mode, newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=CATALOG_FIELDS)
            if not append_mode:
                w.writeheader()
            for u in new_urls:
                try:
                    product = scrape(u, fetch)
                    rows = download_product_images(
                        product, args.images_dir, max_images=args.max_images,
                        max_side=args.max_side, min_side=args.min_side,
                        seen_hashes=seen_hashes, dup_threshold=args.dup_threshold, category=args.category,
                    )
                    written = 0
                    for row in rows:
                        k_path = (row["product_id"], row["image_path"])
                        k_url = (row["product_id"], row["image_url"])
                        if k_path in existing_pairs or k_url in existing_pairs:
                            continue
                        w.writerow(row)
                        existing_pairs.add(k_path)
                        existing_pairs.add(k_url)
                        written += 1
                    existing_product_ids.add(product["product_id"])
                    existing_urls.add(product["url"])
                    fh.flush()
                    prod_count += 1
                    img_count += written
                    print(f"OK   [{product['site']}] {product['isim'][:48] or '(isim yok)'} ({written} görsel)", flush=True)
                except Exception as e:
                    print(f"HATA {u}: {e}", file=sys.stderr, flush=True)
                time.sleep(args.delay)
        print(f"{prod_count} yeni ürün, {img_count} görsel eklendi (atlanan: {skipped_count}) -> {out_path} ({args.images_dir}/)", flush=True)
    finally:
        close_browser()


if __name__ == "__main__":
    main()
