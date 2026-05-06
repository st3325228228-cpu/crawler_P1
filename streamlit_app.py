"""
通用動態爬蟲 + Streamlit UI（四模式比較版・修正版）
"""

import os
import time
import json
import random
import urllib3
import subprocess
import sys
import requests
import cloudscraper
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse, urljoin
import subprocess
import sys

# 確保 Playwright 瀏覽器已安裝
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


urllib3.disable_warnings()


# ══════════════════════════════════════════
# User-Agent
# ══════════════════════════════════════════
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]


def _get_ua():
    return random.choice(_UA_POOL)


def _base_headers(referer="https://www.google.com/"):
    return {
        "User-Agent": _get_ua(),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
        "Referer": referer,
    }


# ══════════════════════════════════════════
# 偵測器
# ══════════════════════════════════════════
class SiteDetector:
    @staticmethod
    def detect(html):
        low = html.lower()
        return {
            "is_cloudflare": any(s in low for s in [
                "cf-browser-verification", "just a moment",
                "checking your browser", "cloudflare ray id",
            ]),
            "is_aspnet": "__viewstate" in low,
            "is_dynamic_js": any(s in html for s in [
                "__NEXT_DATA__", "window.__data", "ng-app",
                "data-reactroot", "vue-app",
            ]),
            "has_table": "<table" in low,
            "has_pagination": any(s in low for s in [
                "下一頁", "next page", "pagination", "page=",
            ]),
            "is_blocked": len(html.strip()) < 500 or any(s in low for s in [
                "access denied", "403 forbidden", "captcha",
            ]),
        }


@dataclass
class ScrapeConfig:
    mode: str = "auto"
    retries: int = 2
    timeout: int = 15
    delay: float = 1.0
    max_pages: int = 1
    page_param: str = "page"
    keyword: str = ""
    extra_headers: dict = field(default_factory=dict)


# ══════════════════════════════════════════
# HTTP 層（修正：SSL fallback / playwright 容錯）
# ══════════════════════════════════════════
class HttpClient:

    def __init__(self, config):
        self.config = config
        self._req_session = self._make_requests_session()
        self._cloud_session = self._make_cloud_session()

    def _make_requests_session(self):
        s = requests.Session()
        s.headers.update(_base_headers())
        if self.config.extra_headers:
            s.headers.update(self.config.extra_headers)
        return s

    def _make_cloud_session(self):
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        s.headers.update(_base_headers())
        s.verify = False  # 🩹 修正：跳過 SSL 驗證
        return s

    def get(self, url, logs):
        mode = self.config.mode

        if mode in ("auto", "requests"):
            html, err = self._get_requests(url, logs)
            if html:
                info = SiteDetector.detect(html)
                if info["is_cloudflare"] or info["is_blocked"]:
                    logs.append("⚠️ requests 被擋，切換 cloudscraper")
                elif info["is_dynamic_js"] and mode == "auto":
                    logs.append("⚠️ JS 框架，切換 playwright")
                    return self._get_playwright(url, logs)
                else:
                    return html, ""
            if mode == "requests":
                return None, err

        if mode in ("auto", "cloudscraper"):
            html, err = self._get_cloud(url, logs)
            if html:
                info = SiteDetector.detect(html)
                if not info["is_blocked"]:
                    return html, ""
                logs.append("⚠️ cloudscraper 被擋，切換 playwright")
            if mode == "cloudscraper":
                return None, err

        if mode in ("auto", "playwright"):
            return self._get_playwright(url, logs)

        return None, "所有模式均失敗"

    def _get_requests(self, url, logs):
        err = ""
        for attempt in range(self.config.retries):
            try:
                self._req_session.headers.update({"User-Agent": _get_ua()})
                try:
                    r = self._req_session.get(url, timeout=self.config.timeout, verify=True)
                except requests.exceptions.SSLError:
                    r = self._req_session.get(url, timeout=self.config.timeout, verify=False)
                r.raise_for_status()
                r.encoding = r.apparent_encoding
                logs.append(f"✅ requests GET [{r.status_code}] {url}")
                return r.text, ""
            except Exception as e:
                err = str(e)
                if attempt < self.config.retries - 1:
                    time.sleep(2 ** attempt)
        logs.append(f"❌ requests 失敗：{err}")
        return None, err

    def _get_cloud(self, url, logs):
        # 🩹 修正：先試 verify=True，失敗則 verify=False
        try:
            r = self._cloud_session.get(url, timeout=self.config.timeout + 5, verify=True)
            r.encoding = r.apparent_encoding
            logs.append(f"✅ cloudscraper GET [{r.status_code}] {url}")
            return r.text, ""
        except (requests.exceptions.SSLError, Exception) as e:
            logs.append(f"⚠️ cloudscraper SSL 問題，改用 verify=False")
            try:
                r = self._cloud_session.get(url, timeout=self.config.timeout + 5, verify=False)
                r.encoding = r.apparent_encoding
                logs.append(f"✅ cloudscraper GET [{r.status_code}] {url}（verify=False）")
                return r.text, ""
            except Exception as e2:
                logs.append(f"❌ cloudscraper 失敗：{e2}")
                return None, str(e2)

    def _get_playwright(self, url, logs):
        # 🩹 修正：先確保 chromium 已裝
        try:
            self._ensure_playwright_browser(logs)
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page()
                page.set_extra_http_headers(_base_headers(url))
                page.goto(url, timeout=30000, wait_until="networkidle")
                time.sleep(2)
                html = page.content()
                browser.close()
            logs.append(f"✅ playwright GET {url}")
            return html, ""
        except Exception as e:
            logs.append(f"❌ playwright 失敗：{e}")
            return None, str(e)

    @staticmethod
    def _ensure_playwright_browser(logs):
        """🩹 確保 chromium 已下載（容器環境用）"""
        cache_dir = os.path.expanduser("~/.cache/ms-playwright")
        if os.path.exists(cache_dir) and any(
            "chrom" in d.lower() for d in os.listdir(cache_dir)
        ):
            return  # 已存在
        logs.append("⏳ Playwright Chromium 未安裝，正在下載...")
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True, timeout=300,
            )
            logs.append("✅ Chromium 安裝完成")
        except Exception as e:
            logs.append(f"⚠️ Chromium 自動安裝失敗：{e}")

    def post(self, url, data, logs):
        try:
            self._req_session.headers.update({"User-Agent": _get_ua()})
            try:
                r = self._req_session.post(url, data=data, timeout=self.config.timeout, verify=True)
            except requests.exceptions.SSLError:
                r = self._req_session.post(url, data=data, timeout=self.config.timeout, verify=False)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            logs.append(f"✅ POST [{r.status_code}] {url}")
            return r.text, ""
        except Exception as e:
            logs.append(f"❌ POST 失敗：{e}")
            return None, str(e)


# ══════════════════════════════════════════
# ASP.NET
# ══════════════════════════════════════════
class AspNetHandler:

    @staticmethod
    def extract_viewstate(html):
        soup = BeautifulSoup(html, "html.parser")
        fields = ["__VIEWSTATE", "__EVENTVALIDATION", "__VIEWSTATEGENERATOR",
                  "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS"]
        payload = {}
        for f in fields:
            tag = soup.find("input", {"name": f}) or soup.find("input", {"id": f})
            if tag:
                payload[f] = tag.get("value", "")
        return payload

    @staticmethod
    def find_query_input(html):
        soup = BeautifulSoup(html, "html.parser")
        for name in ["txtKW", "keyword", "q", "search", "txtKeyword", "kw", "query"]:
            if soup.find("input", {"name": name}):
                return name
        tag = soup.find("input", {"type": "text"})
        return tag["name"] if tag and tag.get("name") else "keyword"

    @staticmethod
    def find_submit_button(html):
        soup = BeautifulSoup(html, "html.parser")
        for btn in soup.find_all("input", {"type": ["submit", "button"]}):
            name = btn.get("name", "")
            val = btn.get("value", "")
            if any(kw in val for kw in ["查詢", "搜尋", "Search", "Submit", "送出"]):
                return {name: val}
        return {}

    @staticmethod
    def paginate_payload(payload, page):
        p = payload.copy()
        p["__EVENTTARGET"] = "GridView1"
        p["__EVENTARGUMENT"] = f"Page${page}"
        return p


# ══════════════════════════════════════════
# Parser（🩹 加上雜訊過濾）
# ══════════════════════════════════════════
# 常見導覽列關鍵字 — 抓到這些就跳過
_NAV_NOISE = {
    "司法院首頁", "裁判書查詢", "簡易案件查詢", "除權判決查詢",
    "公示催告裁定查詢", "司法院資料開放平臺", "本站建議使用",
    "瀏覽人次", "回上一頁", "回首頁", "登入", "註冊",
    "audio版本", "audio_keywords", "non-audio version",
    "audio版本解說", "audio無障礙說明",
}


def _is_noise(text):
    if not text or len(text.strip()) < 5:
        return True
    for kw in _NAV_NOISE:
        if kw in text:
            return True
    return False


class Parser:

    @staticmethod
    def parse(html, url, info):
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
            tag.decompose()

        rows = []
        if info.get("has_table"):
            rows = Parser._tables(soup, url)
        if not rows:
            rows = Parser._lists(soup, url)
        if not rows:
            rows = Parser._paragraphs(soup, url)

        # 🩹 過濾導覽雜訊
        cleaned = []
        for r in rows:
            text_blob = " ".join(str(v) for v in r.values() if v)
            if _is_noise(text_blob):
                continue
            cleaned.append(r)
        return cleaned

    @staticmethod
    def _tables(soup, url):
        rows = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                if headers and len(headers) == len(tds):
                    row = {headers[i]: tds[i].get_text(strip=True) for i in range(len(tds))}
                else:
                    row = {f"欄位{i+1}": td.get_text(strip=True) for i, td in enumerate(tds)}
                a = tr.find("a", href=True)
                if a:
                    row["連結"] = urljoin(url, a["href"])
                if any(v.strip() for v in row.values() if v):
                    rows.append(row)
        return rows

    @staticmethod
    def _lists(soup, url):
        rows = []
        for li in soup.find_all("li")[:300]:
            text = li.get_text(strip=True)
            if len(text) < 5:
                continue
            row = {"內容": text}
            a = li.find("a", href=True)
            if a:
                row["連結"] = urljoin(url, a["href"])
            rows.append(row)
        return rows

    @staticmethod
    def _paragraphs(soup, url):
        rows = []
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "article"]):
            text = tag.get_text(strip=True)
            if len(text) > 15:
                rows.append({"標籤": tag.name, "內容": text, "來源": url})
        return rows[:300]

    @staticmethod
    def find_next_url(html, current_url, page_param, next_page):
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("link", rel="next")
        if tag and tag.get("href"):
            return urljoin(current_url, tag["href"])
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if any(kw in text for kw in ["下一頁", "next", "›", "»", "下頁"]):
                return urljoin(current_url, a["href"])
        parsed = urlparse(current_url)
        qs = parse_qs(parsed.query)
        qs[page_param] = [str(next_page)]
        new_q = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=new_q))


# ══════════════════════════════════════════
# Scraper
# ══════════════════════════════════════════
class UniversalScraper:

    def scrape(self, url, pages=1, keyword="", mode="auto"):
        config = ScrapeConfig(mode=mode, max_pages=pages, keyword=keyword)
        client = HttpClient(config)
        logs = []
        result = {
            "url": url, "mode": mode, "pages_scraped": 0,
            "total_rows": 0, "data": [], "logs": logs, "error": "",
        }

        html, err = client.get(url, logs)
        if not html:
            result["error"] = err
            return result

        info = SiteDetector.detect(html)
        logs.append(f"🔍 偵測：{info}")

        if info["is_aspnet"]:
            logs.append("📋 模式：ASP.NET ViewState")
            data = self._scrape_aspnet(url, html, pages, keyword, config, client, logs)
        else:
            logs.append("🌐 模式：通用爬取")
            data = self._scrape_generic(url, html, pages, keyword, info, config, client, logs)

        if keyword:
            before = len(data)
            data = [r for r in data
                    if keyword.lower() in json.dumps(r, ensure_ascii=False).lower()]
            logs.append(f"🔍 關鍵字「{keyword}」過濾：{before} → {len(data)}")

        result["data"] = data
        result["total_rows"] = len(data)
        result["pages_scraped"] = pages
        return result

    def _scrape_aspnet(self, url, first_html, pages, keyword, config, client, logs):
        data = []
        h = AspNetHandler()

        info = SiteDetector.detect(first_html)
        rows = Parser.parse(first_html, url, info)
        data.extend(rows)
        logs.append(f"第 1 頁：{len(rows)} 筆")

        if pages <= 1:
            return data

        base = h.extract_viewstate(first_html)
        qf = h.find_query_input(first_html)
        sb = h.find_submit_button(first_html)
        if keyword:
            base[qf] = keyword
        base.update(sb)

        for page in range(2, pages + 1):
            payload = h.paginate_payload(base, page)
            html, err = client.post(url, payload, logs)
            if not html:
                break
            info = SiteDetector.detect(html)
            rows = Parser.parse(html, url, info)
            if not rows:
                logs.append(f"ℹ️ 第 {page} 頁無資料，停止")
                break
            data.extend(rows)
            logs.append(f"第 {page} 頁：{len(rows)} 筆")
            base.update(h.extract_viewstate(html))
            time.sleep(config.delay)
        return data

    def _scrape_generic(self, url, first_html, pages, keyword, info, config, client, logs):
        data = []
        current_url = url
        html = first_html

        for page in range(1, pages + 1):
            if page > 1:
                next_url = Parser.find_next_url(html, current_url, config.page_param, page)
                if not next_url or next_url == current_url:
                    break
                current_url = next_url
                html, err = client.get(current_url, logs)
                if not html:
                    break
                info = SiteDetector.detect(html)
                time.sleep(config.delay)

            if info["is_blocked"]:
                break

            rows = Parser.parse(html, current_url, info)
            if not rows:
                break
            data.extend(rows)
            logs.append(f"第 {page} 頁：{len(rows)} 筆")
        return data


# ══════════════════════════════════════════
# Streamlit
# ══════════════════════════════════════════
@st.cache_resource
def get_scraper():
    return UniversalScraper()


def detect_used_mode(logs):
    for log in logs:
        if "playwright" in log: return "playwright"
        if "cloudscraper" in log: return "cloudscraper"
        if "requests" in log: return "requests"
    return "auto"


st.set_page_config(page_title="🕷️ 通用爬蟲", page_icon="🕷️", layout="wide")
scraper = get_scraper()

st.title("🕷️ 通用動態爬蟲")
st.caption("支援靜態 / ASP.NET / Cloudflare / JS 動態網站 · 四模式比較")

st.divider()

url_input = st.text_input("🌐 目標網址",
    placeholder="https://judgment.judicial.gov.tw/FJUD/default.aspx")

st.markdown("**⚙️ 選擇要執行的模式**")
mc1, mc2, mc3, mc4 = st.columns(4)
with mc1: use_auto = st.checkbox("auto", value=True)
with mc2: use_req = st.checkbox("requests", value=True)
with mc3: use_cloud = st.checkbox("cloudscraper", value=True)
with mc4: use_pw = st.checkbox("playwright", value=True)

c3, c4 = st.columns([3, 2])
with c3: page_input = st.slider("📄 爬取頁數", 1, 20, 1, 1)
with c4: keyword_input = st.text_input("🔍 關鍵字過濾（選填）")

scrape_btn = st.button("🚀 開始爬取", type="primary", use_container_width=True)

st.divider()

if scrape_btn:
    if not url_input.strip():
        st.error("⚠️ 請輸入網址")
    else:
        modes = []
        if use_auto: modes.append("auto")
        if use_req: modes.append("requests")
        if use_cloud: modes.append("cloudscraper")
        if use_pw: modes.append("playwright")

        if not modes:
            st.error("⚠️ 請至少勾選一個模式")
        else:
            results = {}
            progress = st.progress(0, text="開始執行...")

            for i, m in enumerate(modes):
                progress.progress(i / len(modes),
                    text=f"🕸️ 執行 {m} ({i+1}/{len(modes)})...")
                try:
                    r = scraper.scrape(url_input.strip(),
                        int(page_input), keyword_input.strip(), m)
                except Exception as e:
                    r = {"url": url_input, "mode": m, "pages_scraped": 0,
                         "total_rows": 0, "data": [],
                         "logs": [f"❌ 例外：{e}"], "error": str(e)}
                results[m] = r

            progress.progress(1.0, text="✅ 完成")
            time.sleep(0.3); progress.empty()

            # 比較表
            st.subheader("📊 模式比較總覽")
            cmp = []
            for m, r in results.items():
                cmp.append({
                    "模式": m,
                    "狀態": "✅ 成功" if not r.get("error") else "❌ 失敗",
                    "總筆數": r.get("total_rows", 0),
                    "爬取頁數": r.get("pages_scraped", 0),
                    "實際使用": detect_used_mode(r.get("logs", [])),
                    "錯誤訊息": (r.get("error", "") or "—")[:80],
                })
            st.dataframe(pd.DataFrame(cmp), use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("📁 各模式詳細結果")
            tabs = st.tabs([f"🔧 {m}" for m in modes])

            for tab, m in zip(tabs, modes):
                with tab:
                    r = results[m]
                    err = r.get("error", "")
                    if not err:
                        st.success(f"✅ {m} 成功")
                    else:
                        st.error(f"❌ {m}：{err[:200]}")

                    a, b, c, d = st.columns(4)
                    a.metric("總筆數", r.get("total_rows", 0))
                    b.metric("爬取頁數", r.get("pages_scraped", 0))
                    c.metric("實際使用", detect_used_mode(r.get("logs", [])))
                    d.metric("關鍵字", keyword_input or "—")

                    s1, s2, s3 = st.tabs(["📋 表格", "🗂️ JSON", "📝 日誌"])
                    data = r.get("data", [])
                    with s1:
                        if data:
                            df = pd.DataFrame(data)
                            st.dataframe(df, use_container_width=True, height=450)
                            csv = df.to_csv(index=False).encode("utf-8-sig")
                            st.download_button(f"⬇️ CSV ({m})", csv,
                                f"scrape_{m}.csv", "text/csv", key=f"csv_{m}")
                        else:
                            st.info("（無資料）")
                    with s2:
                        raw = json.dumps(r, ensure_ascii=False, indent=2)
                        st.code(raw, language="json")
                        st.download_button(f"⬇️ JSON ({m})", raw.encode("utf-8"),
                            f"scrape_{m}.json", "application/json", key=f"json_{m}")
                    with s3:
                        logs = "\n".join(r.get("logs", []))
                        if logs: st.code(logs, language="text")
                        else: st.info("（無日誌）")

            st.divider()
            merged = json.dumps(results, ensure_ascii=False, indent=2)
            st.download_button("⬇️ 下載全部模式結果（JSON）",
                merged.encode("utf-8"), "scrape_all_modes.json",
                "application/json", use_container_width=True)
else:
    st.info("👆 請輸入網址、勾選模式後點擊「開始爬取」")
