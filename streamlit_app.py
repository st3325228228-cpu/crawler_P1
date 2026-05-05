import streamlit as st
import json
import pandas as pd
import subprocess
import sys
from scraper_universal import UniversalScraper

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


install_playwright()
scraper = get_scraper()


# ══════════════════════════════════════════
# 核心爬取邏輯
# ══════════════════════════════════════════
def run_scrape(url: str, pages: int, keyword: str, mode: str):
    if not url.strip():
        return None, "請輸入網址"

    result = scraper.scrape(
        url=url.strip(),
        pages=int(pages),
        keyword=keyword.strip(),
        mode=mode,
    )
    return result, None


def detect_used_mode(logs_list):
    """從 log 推斷實際使用的模式"""
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
            result, err = run_scrape(url_input, page_input, keyword_input, mode_input)

        if err:
            st.error(err)
        else:
            logs_list = result.get("logs", [])
            logs_str = "\n".join(logs_list)
            used_mode = detect_used_mode(logs_list)
            error_msg = result.get("error", "")

            # ── 摘要區 ──
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
