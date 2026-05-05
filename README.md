# 🕷️ 通用動態爬蟲 Universal Web Scraper

一個基於 **Streamlit** 的通用爬蟲工具，支援靜態網頁、ASP.NET ViewState、Cloudflare 防護以及 JavaScript 動態網站，能一次跑四種模式進行比較。

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Streamlit](https://img.shields.io/badge/streamlit-1.30+-red.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## ✨ 功能特色

- 🔄 **四模式並跑** — `auto` / `requests` / `cloudscraper` / `playwright` 一次比較
- 🛡️ **自動偵測** — Cloudflare、ASP.NET、JS 框架自動切換策略
- 📋 **ASP.NET ViewState** — 自動處理 `__VIEWSTATE` 翻頁
- 🌐 **多種解析** — Table / List / Paragraph 三層 fallback
- 🔍 **關鍵字過濾** — 即時篩選結果
- 📥 **一鍵下載** — CSV / JSON 雙格式

---

## 🚀 快速開始

### 本機執行

```bash
# 1. Clone 專案
git clone https://github.com/<你的帳號>/<repo名稱>.git
cd <repo名稱>

# 2. 安裝套件
pip install -r requirements.txt

# 3. 安裝 Playwright 瀏覽器
playwright install chromium

# 4. 啟動
streamlit run app.py
```

開啟瀏覽器到 `http://localhost:8501` 即可。

---

## 🌐 部署到 Streamlit Cloud

1. 將 repo 推上 GitHub（需 Public）
2. 前往 [share.streamlit.io](https://share.streamlit.io)
3. 點擊 **New app**，選擇 repo 與 `app.py`
4. 完成！自動部署 🎉

---

## 📦 專案結構

```
.
├── app.py                  # 主程式
├── requirements.txt        # Python 依賴
├── packages.txt            # 系統依賴
├── .streamlit/config.toml  # 主題設定
└── README.md
```

---

## 🛠️ 使用方式

1. 輸入目標網址
2. 勾選想跑的模式（預設四個都勾）
3. 設定爬取頁數與關鍵字
4. 點擊 **🚀 開始爬取**
5. 在比較表 / 各模式 Tab 中查看結果

---

## ⚠️ 注意事項

- 請遵守目標網站的 `robots.txt` 與服務條款
- 避免高頻率請求造成對方伺服器負擔
- 部分網站可能需要登入 Cookie，本工具未實作此功能

---

## 📜 授權

MIT License — 自由使用、修改、散布

---

## 🤝 貢獻

歡迎 PR 與 Issue！
