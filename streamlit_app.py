"""
通用動態爬蟲 + Streamlit UI（單檔整合版）
支援：靜態 / Cloudflare / ASP.NET ViewState / JS 動態（Playwright）
"""

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
from lxml import etree
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings()


# ══════════════════════════════════════════
# User-Agent 池
# ══════════════════════════════════════════
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1",
]


def _get_ua() -> str:
    return random.choice(_UA_POOL)


def _base_headers(referer: str = "https://www.google.com/") -> dict:
    return {
        "User-Agent":                _get_ua(),
        "Accept":                    "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language":           "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept-Encoding":           "gzip, deflate, br",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control":             "max-age=0",
        "DNT":                       "1",
        "Referer":                   referer,
    }


# ══════════════════════════════════════════
# 網站類型偵測器
# ══════════════════════════════════════════
class SiteDetector:
    @staticmethod
    def detect(html: str) -> dict:
        low = html.lower()
        return {
            "is_cloudflare": any(s in low for s in [
                "cf-browser-verification", "just a moment",
                "enable javascript", "checking your browser",
                "ddos-guard", "cloudflare ray id",
            ]),
            "is_aspnet": "__viewstate" in low,
            "is_dynamic_js": any(s in html for s in [
                "__NEXT_DATA__", "window.__data", "ng-app",
                "data-reactroot", "vue-app",
            ]),
            "has_table": "<table" in low,
            "has_pagination": any(s in low for s in [
                "下一頁", "next page", "pagination",
                "page=", "&amp;page",
            ]),
            "is_blocked": len(html.strip()) < 500 or any(s in low for s in [
                "access denied", "403 forbidden",
                "robot check", "captcha",
            ]),
        }


# ══════════════════════════════════════════
# 設定
# ══════════════════════════════════════════
@dataclass
class ScrapeConfig:
    mode:       str   = "auto"
    retries:    int   = 2
    timeout:    int   = 15
    delay:      float = 1.0
    max_pages:  int   = 1
    page_param: str   = "page"
    keyword:    str   = ""
    aspnet_query_field:  str = ""
    aspnet_submit_field: str = ""
    extra_headers: dict = field(default_factory=dict)


# ══════════════════════════════════════════
# HTTP 層
# ══════════════════════════════════════════
class HttpClient:

    def __init__(self, config: ScrapeConfig):
        self.config = config
        self._req_session   = self._make_requests_session()
        self._cloud_session = self._make_cloud_session()

    def _make_requests_session(self) -> requests.Session:
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
        return s

    def get(self, url: str, logs: list):
        mode = self.config.mode

        if mode in ("auto", "requests"):
            html, err = self._get_requests(url, logs)
            if html:
                info = SiteDetector.detect(html)
                if info["is_cloudflare"] or info["is_blocked"]:
                    logs.append("⚠️ requests 被擋，切換 cloudscraper")
                elif info["is_dynamic_js"] and mode == "auto":
                    logs.append("⚠️ 偵測到 JS 框架，切換 playwright")
                    return self._get_playwright(url, logs)
                else:
                    return html, ""

        if mode in ("auto", "cloudscraper"):
            html, err = self._get_cloud(url, logs)
            if html:
                info = SiteDetector.detect(html)
                if not info["is_blocked"]:
                    return html, ""
                logs.append("⚠️ cloudscraper 也被擋，切換 playwright")

        if mode in ("auto", "playwright"):
            return self._get_playwright(url, logs)

        return None, "所有模式均失敗"

    def _get_requests(self, url: str, logs: list):
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

    def _get_cloud(self, url: str, logs: list):
        try:
            r = self._cloud_session.get(url, timeout=self.config.timeout + 5)
            r.encoding = r.apparent_encoding
            logs.append(f"✅ cloudscraper GET [{r.status_code}] {url}")
            return r.text, ""
        except Exception as e:
            logs.append(f"❌ cloudscraper 失敗：{e}")
            return None, str(e)

    def _get_playwright(self, url: str, logs: list):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page    = browser.new_page()
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

    def post(self, url: str, data: dict, logs: list):
        try:
            self._req_session.headers.update({"User-Agent": _get_ua()})
            try:
                r = self._req_session.post(
                    url, data=data,
                    timeout=self.config.timeout, verify=True
                )
            except requests.exceptions.SSLError:
                r = self._req_session.post(
                    url, data=data,
                    timeout=self.config.timeout, verify=False
                )
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            logs.append(f"✅ POST [{r.status_code}] {url}")
            return r.text, ""
        except Exception as e:
            logs.append(f"❌ POST 失敗：{e}")
            return None, str(e)


# ══════════════════════════════════════════
# ASP.NET 處理器
# ══════════════════════════════════════════
class AspNetHandler:

    @staticmethod
    def extract_viewstate(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        fields = [
            "__VIEWSTATE", "__EVENTVALIDATION",
            "__VIEWSTATEGENERATOR", "__EVENTTARGET",
            "__EVENTARGUMENT", "__LASTFOCUS",
        ]
        payload = {}
        for f in fields:
            tag = soup.find("input", {"name": f}) or soup.find("input", {"id": f})
            if tag:
                payload[f] = tag.get("value", "")
        return payload

    @staticmethod
    def find_query_input(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        candidates = [
            "txtKW", "keyword", "q", "search",
            "txtKeyword", "kw", "query", "txtSearch",
        ]
        for name in candidates:
            if soup.find("input", {"name": name}):
                return name
        tag = soup.find("input", {"type": "text"})
        return tag["name"] if tag and tag.get("name") else "keyword"

    @staticmethod
    def find_submit_button(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        for btn in soup.find_all("input", {"type": ["submit", "button"]}):
            name = btn.get("name", "")
            val  = btn.get("value", "")
            if any(kw in val for kw in ["查詢", "搜尋", "Search", "Submit", "送出"]):
                return {name: val}
        return {}

    @staticmethod
    def paginate_payload(payload: dict, page: int) -> dict:
        p = payload.copy()
        p["__EVENTTARGET"]   = "GridView1"
        p["__EVENTARGUMENT"] = f"Page${page}"
        return p


# ══════════════════════════════════════════
# 解析層
# ══════════════════════════════════════════
class Parser:

    @staticmethod
    def parse(html: str, url: str, info: dict) -> list:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav",
                         "footer", "header", "noscript", "iframe"]):
            tag.decompose()

        rows = []
        if info.get("has_table"):
            rows = Parser._tables(soup, url)
        if not rows:
            rows = Parser._lists(soup, url)
        if not rows:
            rows = Parser._paragraphs(soup, url)
        return rows

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
                    row = {headers[i]: tds[i].get_text(strip=True)
                           for i in range(len(tds))}
                else:
                    row = {f"欄位{i+1}": td.get_text(strip=True)
                           for i, td in enumerate(tds)}
                a = tr.find("a", href=True)
                if a:
                    row["連結"] = urljoin(url, a["href"])
                if any(v.strip() for v in row.values()):
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
            a   = li.find("a", href=True)
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
# 通用爬蟲主類別
# ══════════════════════════════════════════
class UniversalScraper:

    def scrape(self, url: str, pages: int = 1,
               keyword: str = "", mode: str = "auto") -> dict:
        config = ScrapeConfig(mode=mode, max_pages=pages, keyword=keyword)
        client = HttpClient(config)
        logs   = []
        result = {
            "url": url, "pages_scraped": 0,
            "total_rows": 0, "data": [], "logs": logs,
        }

        html, err = client.get(url, logs)
        if not html:
            result["error"] = err
            return result

        info = SiteDetector.detect(html)
        logs.append(f"🔍 偵測：{info}")

        if info["is_aspnet"]:
            logs.append("📋 模式：ASP.NET ViewState")
            all_data = self._scrape_aspnet(
                url, html, pages, keyword, config, client, logs
            )
        else:
            logs.append("🌐 模式：通用爬取")
            all_data = self._scrape_generic(
                url, html, pages, keyword, info, config, client, logs
            )

        if keyword:
            before = len(all_data)
            all_data = [
                r for r in all_data
                if keyword.lower() in
                   json.dumps(r, ensure_ascii=False).lower()
            ]
            logs.append(f"🔍 關鍵字「{keyword}」過濾：{before} → {len(all_data)} 筆")

        result["data"]          = all_data
        result["total_rows"]    = len(all_data)
        result["pages_scraped"] = pages
        return result

    def _scrape_aspnet(self, url, first_html, pages,
                       keyword, config, client, logs):
        all_data = []
        handler  = AspNetHandler()

        info = SiteDetector.detect(first_html)
        rows = Parser.parse(first_html, url, info)
        all_data.extend(rows)
        logs.append(f"第 1 頁：{len(rows)} 筆")

        if pages <= 1:
            return all_data

        base_payload = handler.extract_viewstate(first_html)
        query_field  = (config.aspnet_query_field
                        or handler.find_query_input(first_html))
        submit_btn   = (config.aspnet_submit_field
                        or handler.find_submit_button(first_html))

        if keyword:
            base_payload[query_field] = keyword
        base_payload.update(submit_btn)

        for page in range(2, pages + 1):
            payload = handler.paginate_payload(base_payload, page)
            html, err = client.post(url, payload, logs)
            if not html:
                logs.append(f"⚠️ 第 {page} 頁 POST 失敗：{err}")
                break
            info = SiteDetector.detect(html)
            rows = Parser.parse(html, url, info)
            if not rows:
                logs.append(f"ℹ️ 第 {page} 頁無資料，停止")
                break
            all_data.extend(rows)
            logs.append(f"第 {page} 頁：{len(rows)} 筆")
            base_payload.update(handler.extract_viewstate(html))
            time.sleep(config.delay)

        return all_data

    def _scrape_generic(self, url, first_html, pages,
                        keyword, info, config, client, logs):
        all_data    = []
        current_url = url
        html        = first_html

        for page in range(1, pages + 1):
            if page > 1:
                next_url = Parser.find_next_url(
                    html, current_url, config.page_param, page
                )
                if not next_url or next_url == current_url:
                    logs.append(f"ℹ️ 找不到第 {page} 頁，停止")
                    break
                current_url = next_url
                html, err   = client.get(current_url, logs)
                if not html:
                    logs.append(f"⚠️ 第 {page} 頁失敗：{err}")
                    break
                info = SiteDetector.detect(html)
                time.sleep(config.delay)

            if info["is_blocked"]:
                logs.append(f"🚫 第 {page} 頁被擋")
                break

            rows = Parser.parse(html, current_url, info)
            if not rows:
                logs.append(f"⚠️ 第 {page} 頁解析不到內容")
                break

            all_data.extend(rows)
            logs.append(f"第 {page} 頁：{len(rows)} 筆（{current_url}）")

        return all_data

    def scrape_multiple(self, urls, pages=1, keyword="",
                        mode="auto", max_workers=5):
        all_data, all_logs = [], []

        def _task(url):
            return self.scrape(url, pages, keyword, mode)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_task, u): u for u in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    r = future.result()
                    all_data.extend(r.get("data", []))
                    all_logs.extend(r.get("logs", []))
                except Exception as e:
                    all_logs.append(f"⚠️ {url} 執行緒錯誤：{e}")

        return {
            "total_rows": len(all_data),
            "data":       all_data,
            "logs":       all_logs,
        }


# ══════════════════════════════════════════
# Playwright 安裝（首次啟動時執行，會被快取）
# ══════════════════════════════════════════
@st.cache_resource(show_spinner="正在初始化 Playwright...")
def install_playwright():
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=True, capture_output=True
        )
        return True
    except Exception as e:
        print(f"⚠️ Playwright 安裝失敗：{e}")
        return False


@st.cache_resource
def get_scraper():
    return UniversalScraper()


def detect_used_mode(logs_list):
    for log in logs_list:
        if "playwright" in log:
            return "playwright"
        if "cloudscraper" in log:
            return "cloudscraper"
        if "requests" in log:
            return "requests"
    return "auto"


# ══════════════════════════════════════════
# Streamlit UI
# ══════════════════════════════════════════
st.set_page_config(
    page_title="🕷️ 通用爬蟲",
    page_icon="🕷️",
    layout="wide",
)

install_playwright()
scraper = get_scraper()

st.title("🕷️ 通用動態爬蟲")
st.caption(
    "支援 **靜態網頁 / ASP.NET / Cloudflare / JavaScript 動態** 網站 · "
    "自動偵測模式，無需手動設定"
)

st.divider()

# ── 輸入區 ────────────────────────────
col1, col2 = st.columns([4, 1])
with col1:
    url_input = st.text_input(
        "🌐 目標網址",
        placeholder="https://judgment.judicial.gov.tw/FJUD/default.aspx",
    )
with col2:
    mode_input = st.selectbox(
        "⚙️ 模式",
        options=["auto", "requests", "cloudscraper", "playwright"],
        index=0,
    )

col3, col4 = st.columns([3, 2])
with col3:
    page_input = st.slider(
        "📄 爬取頁數",
        min_value=1, max_value=20, value=1, step=1,
    )
with col4:
    keyword_input = st.text_input(
        "🔍 關鍵字過濾（選填）",
        placeholder="例：勞動契約",
    )

scrape_btn = st.button("🚀 開始爬取", type="primary", use_container_width=True)

st.divider()

# ── 執行爬取 ──────────────────────────
if scrape_btn:
    if not url_input.strip():
        st.error("⚠️ 請輸入網址")
    else:
        with st.spinner("🕸️ 正在爬取資料中，請稍候..."):
            result = scraper.scrape(
                url=url_input.strip(),
                pages=int(page_input),
                keyword=keyword_input.strip(),
                mode=mode_input,
            )

        logs_list = result.get("logs", [])
        logs_str  = "\n".join(logs_list)
        used_mode = detect_used_mode(logs_list)
        error_msg = result.get("error", "")

        # ── 摘要 ──
        st.subheader("📊 爬取摘要")

        if not error_msg:
            st.success("✅ 爬取完成")
        else:
            st.error(f"❌ 爬取失敗：{error_msg}")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("📊 總筆數", result.get("total_rows", 0))
        m2.metric("📄 爬取頁數", result.get("pages_scraped", 0))
        m3.metric("🔧 實際模式", used_mode)
        m4.metric("🔍 關鍵字", keyword_input if keyword_input else "（無）")

        with st.expander("📌 詳細資訊", expanded=False):
            st.write(f"**網址：** {result.get('url', url_input)}")
            st.write(f"**錯誤：** {error_msg if error_msg else '無'}")

        st.divider()

        # ── Tabs 輸出 ──
        tab1, tab2, tab3 = st.tabs(["📋 表格檢視", "🗂️ JSON 原始資料", "📝 執行日誌"])

        data = result.get("data", [])

        with tab1:
            if data:
                df = pd.DataFrame(data)
                st.dataframe(df, use_container_width=True, height=500)
                csv = df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "⬇️ 下載 CSV",
                    data=csv,
                    file_name="scrape_result.csv",
                    mime="text/csv",
                )
            else:
                st.info("（無資料）")

        with tab2:
            raw = json.dumps(result, ensure_ascii=False, indent=2)
            st.code(raw, language="json")
            st.download_button(
                "⬇️ 下載 JSON",
                data=raw.encode("utf-8"),
                file_name="scrape_result.json",
                mime="application/json",
            )

        with tab3:
            if logs_str:
                st.code(logs_str, language="text")
            else:
                st.info("（無日誌）")
else:
    st.info("👆 請輸入網址並點擊「開始爬取」")
