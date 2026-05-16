import streamlit as st
import requests
import os
import re
import subprocess
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ZOTERO_EXE = r"C:\Program Files\Zotero\zotero.exe"
BASE_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


def has_chinese(text):
    return bool(re.search(r"[一-鿿]", text))


def translate_to_english(text):
    if not GROQ_API_KEY:
        return text
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "Translate the following academic research topic from Chinese to English. Output only the translated English keywords, nothing else."},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.1,
                "max_tokens": 100,
            },
            timeout=15,
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return text

st.set_page_config(page_title="BibTeX 文獻產生器", page_icon="📚", layout="centered")
st.title("📚 BibTeX 文獻產生器")
st.caption("搜尋真實學術論文 → 匯入 Zotero → 下載 PDF，一鍵完成")

st.info("📡 資料來源：OpenAlex — 2.5 億篇真實論文，結果 100% 真實存在")

topic = st.text_input("研究主題", placeholder="例如：machine learning in medical imaging")

col1, col2, col3 = st.columns(3)
with col1:
    count = st.select_slider("文獻數量", options=[10, 20, 30, 50], value=20)
with col2:
    sort = st.radio("排序方式", ["引用次數", "最新發表"], horizontal=True)
with col3:
    lang = st.radio("語言", ["不限", "英文", "中文"], horizontal=True)

only_oa = st.checkbox("只顯示可免費下載的論文（開放取用）", value=True)

JOURNAL_GROUPS = {
    "不限": [],
    "自然科學頂刊": ["Nature", "Science", "Cell"],
    "AI / ML 期刊": ["Nature Machine Intelligence", "Journal of Machine Learning Research", "IEEE Transactions on Pattern Analysis and Machine Intelligence"],
    "醫學頂刊": ["New England Journal of Medicine", "The Lancet", "JAMA", "BMJ"],
    "綜合開放取用": ["PLOS ONE", "Scientific Reports", "Nature Communications"],
}

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_source_ids(journal_names):
    ids = []
    for name in journal_names:
        try:
            r = requests.get(
                "https://api.openalex.org/sources",
                params={"search": name, "per-page": 1, "mailto": "bensonlai94531@gmail.com"},
                timeout=10,
            )
            results = r.json().get("results", [])
            if results:
                ids.append(results[0]["id"].split("/")[-1])
        except Exception:
            pass
    return ids

INSTITUTION_GROUPS = {
    "不限": [],
    "世界頂尖大學": ["Massachusetts Institute of Technology", "Stanford University", "Harvard University", "University of Oxford", "University of Cambridge", "ETH Zurich", "Caltech"],
    "亞洲頂尖大學": ["National University of Singapore", "University of Tokyo", "Tsinghua University", "Peking University", "Seoul National University"],
    "台灣頂尖大學": ["National Taiwan University", "National Tsing Hua University", "National Chiao Tung University", "Academia Sinica"],
}

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_institution_ids(institution_names):
    ids = []
    for name in institution_names:
        try:
            r = requests.get(
                "https://api.openalex.org/institutions",
                params={"search": name, "per-page": 1, "mailto": "bensonlai94531@gmail.com"},
                timeout=10,
            )
            results = r.json().get("results", [])
            if results:
                ids.append(results[0]["id"].split("/")[-1])
        except Exception:
            pass
    return ids

journal_group = st.selectbox("期刊類別篩選", list(JOURNAL_GROUPS.keys()))
institution_group = st.selectbox("機構篩選（作者所屬大學）", list(INSTITUTION_GROUPS.keys()))
min_citations = st.select_slider("最低引用次數（篩選知名學者論文）", options=[0, 10, 50, 100, 500, 1000], value=10)


def make_citation_key(authors, year, title):
    last_name = authors[0].split()[-1] if authors else "unknown"
    last_name = re.sub(r"[^a-zA-Z]", "", last_name).lower()
    first_word = re.sub(r"[^a-zA-Z]", "", title.split()[0]).lower() if title else "paper"
    return f"{last_name}{year}{first_word}"


def to_bibtex(work):
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in work.get("authorships", [])
    ]
    year = work.get("publication_year") or "n.d."
    title = work.get("title") or "Untitled"
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    journal = source.get("display_name", "")
    doi_url = work.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "")
    work_type = work.get("type", "")
    landing_url = loc.get("landing_page_url") or ""
    oa_url = (work.get("open_access") or {}).get("oa_url") or ""
    url = doi_url or landing_url or oa_url

    key = make_citation_key(authors, year, title)

    if work_type == "article":
        entry_type = "@article"
    elif work_type == "proceedings-article":
        entry_type = "@inproceedings"
    elif work_type in ("book", "book-chapter"):
        entry_type = "@book"
    else:
        entry_type = "@misc"

    lines = [f"{entry_type}{{{key},"]
    lines.append(f"  author = {{{' and '.join(authors)}}},")
    lines.append(f"  title = {{{{{title}}}}},")
    lines.append(f"  year = {{{year}}},")
    if journal:
        lines.append(f"  journal = {{{journal}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if url:
        lines.append(f"  url = {{{url}}},")
    lines.append("}")
    return "\n".join(lines)


def resolve_pdf_url(url):
    if not url:
        return url
    url = re.sub(r"arxiv\.org/abs/", "arxiv.org/pdf/", url)
    pmc = re.search(r"pmc\.ncbi\.nlm\.nih\.gov/articles/(PMC\d+)", url)
    if pmc:
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc.group(1)}/pdf/"
    return url


def get_unpaywall_pdf(doi):
    if not doi:
        return None
    try:
        r = requests.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": "bensonlai94531@gmail.com"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            best = data.get("best_oa_location") or {}
            return best.get("url_for_pdf")
    except Exception:
        pass
    return None


def download_pdfs(works, pdf_dir):
    downloaded = []
    logs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
    }
    for w in works:
        title = w.get("title") or "Untitled"
        doi_url = w.get("doi") or ""
        doi = doi_url.replace("https://doi.org/", "")
        oa = w.get("open_access") or {}
        oa_raw = oa.get("oa_url") or (w.get("primary_location") or {}).get("pdf_url")
        unpaywall_url = get_unpaywall_pdf(doi)
        pdf_url = resolve_pdf_url(unpaywall_url or oa_raw)

        if not pdf_url:
            logs.append(f"無 PDF　{title}")
            continue

        safe = re.sub(r'[\\/:*?"<>|]', "_", title[:100]) + ".pdf"
        pdf_path = os.path.join(pdf_dir, safe)
        try:
            r = requests.get(pdf_url, timeout=20, headers=headers, allow_redirects=True)
            if r.status_code == 200 and b"%PDF" in r.content[:10]:
                with open(pdf_path, "wb") as f:
                    f.write(r.content)
                downloaded.append(pdf_path)
                logs.append(f"✅ 已下載　{title}")
            elif r.status_code == 403:
                logs.append(f"🔒 伺服器拒絕存取　{title}")
            else:
                logs.append(f"❌ 非直接 PDF（{r.status_code}）　{title}")
        except Exception as e:
            logs.append(f"❌ 失敗（{e}）　{title}")
    return downloaded, logs


if st.button("開始：搜尋 → 匯入 → 下載 PDF", type="primary", disabled=not topic):

    # 步驟一：搜尋論文
    with st.status("步驟 1 / 3　正在搜尋論文...", expanded=True) as status:
        try:
            search_topic = topic
            if has_chinese(topic):
                st.write("🌐 偵測到中文，正在翻譯為英文搜尋關鍵字...")
                search_topic = translate_to_english(topic)
                st.write(f"📝 翻譯結果：**{search_topic}**")

            sort_param = "cited_by_count:desc" if sort == "引用次數" else "publication_date:desc"
            lang_map = {"英文": "en", "中文": "zh"}
            params = {
                "per-page": count,
                "sort": sort_param,
                "mailto": "bensonlai94531@gmail.com",
                "select": "title,authorships,publication_year,primary_location,doi,type,cited_by_count,open_access,ids",
            }
            filters = [f"title.search:{search_topic}"]
            if lang in lang_map:
                filters.append(f"language:{lang_map[lang]}")
            if only_oa:
                filters.append("is_oa:true")
            if journal_group != "不限":
                source_ids = fetch_source_ids(tuple(JOURNAL_GROUPS[journal_group]))
                if source_ids:
                    filters.append("primary_location.source.id:" + "|".join(source_ids))
            if institution_group != "不限":
                inst_ids = fetch_institution_ids(tuple(INSTITUTION_GROUPS[institution_group]))
                if inst_ids:
                    filters.append("authorships.institutions.id:" + "|".join(inst_ids))
            if min_citations > 0:
                filters.append(f"cited_by_count:>{min_citations}")
            if filters:
                params["filter"] = ",".join(filters)

            resp = requests.get("https://api.openalex.org/works", params=params, timeout=20)
            resp.raise_for_status()
            raw = resp.json().get("results", [])

            # 去除重複（同 DOI 優先，否則同標題）
            seen = set()
            works = []
            for w in raw:
                key = w.get("doi") or (w.get("title") or "").strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    works.append(w)

            if not works:
                st.warning("找不到相關文獻，請嘗試其他關鍵字。")
                st.stop()

            topic_folder = re.sub(r'[\\/:*?"<>|]', "_", topic.strip())[:50]
            output_dir = os.path.join(BASE_OUTPUT_DIR, topic_folder)
            pdf_dir = os.path.join(output_dir, "pdfs")
            os.makedirs(pdf_dir, exist_ok=True)

            bibtex = "\n\n".join(to_bibtex(w) for w in works)
            filename = f"{topic_folder}.bib"
            bib_path = os.path.join(output_dir, filename)
            with open(bib_path, "w", encoding="utf-8") as f:
                f.write(bibtex)

            # 產生 HTML 來源清單
            rows = []
            for i, w in enumerate(works, 1):
                title = w.get("title") or "Untitled"
                doi_url = w.get("doi") or ""
                loc = w.get("primary_location") or {}
                landing = loc.get("landing_page_url") or ""
                oa_url = (w.get("open_access") or {}).get("oa_url") or ""
                link = doi_url or landing or oa_url
                year = w.get("publication_year") or "n.d."
                source = (loc.get("source") or {}).get("display_name", "未知期刊")
                title_html = f'<a href="{link}" target="_blank">{title}</a>' if link else title
                rows.append(f"""
                <tr>
                  <td>{i}</td>
                  <td>{title_html}</td>
                  <td>{source}</td>
                  <td>{year}</td>
                </tr>""")

            html_content = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>文獻來源清單：{topic}</title>
  <style>
    body {{ font-family: sans-serif; padding: 24px; max-width: 960px; margin: auto; }}
    h1 {{ font-size: 1.4em; color: #333; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th {{ background: #f0f0f0; text-align: left; padding: 8px 12px; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
    a {{ color: #0066cc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>文獻來源清單：{topic}</h1>
  <table>
    <thead><tr><th>#</th><th>標題</th><th>期刊</th><th>年份</th></tr></thead>
    <tbody>{"".join(rows)}
    </tbody>
  </table>
</body>
</html>"""

            sources_path = os.path.join(output_dir, "sources.html")
            with open(sources_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            status.update(label=f"步驟 1 / 3　找到 {len(works)} 篇論文 ✅", state="complete")
            st.caption(f"📁 儲存位置：output/{topic_folder}/")

            st.subheader("📋 文獻來源清單")
            for i, w in enumerate(works, 1):
                title = w.get("title") or "Untitled"
                doi_url = w.get("doi") or ""
                loc = w.get("primary_location") or {}
                landing = loc.get("landing_page_url") or ""
                oa_url = (w.get("open_access") or {}).get("oa_url") or ""
                link = doi_url or landing or oa_url
                year = w.get("publication_year") or "n.d."
                source = (loc.get("source") or {}).get("display_name", "未知期刊")
                if link:
                    st.markdown(f"{i}. [{title}]({link}) — *{source}*, {year}")
                else:
                    st.markdown(f"{i}. {title} — *{source}*, {year}")
        except Exception as e:
            st.error(f"搜尋失敗：{e}")
            st.stop()

    # 步驟二：匯入 Zotero
    with st.status("步驟 2 / 3　正在匯入 Zotero...", expanded=True) as status:
        if os.path.exists(ZOTERO_EXE):
            subprocess.Popen([ZOTERO_EXE, "-file", bib_path])
            status.update(label="步驟 2 / 3　已匯入 Zotero ✅", state="complete")
        else:
            status.update(label="步驟 2 / 3　找不到 Zotero，略過", state="error")

    # 步驟三：下載 PDF
    with st.status("步驟 3 / 3　正在下載開放取用 PDF...", expanded=True) as status:
        downloaded, logs = download_pdfs(works, pdf_dir)
        for log in logs:
            st.write(log)
        status.update(
            label=f"步驟 3 / 3　下載完成：{len(downloaded)}/{len(works)} 篇 ✅",
            state="complete",
        )

    # 結果摘要
    st.success(f"全部完成！{len(works)} 篇文獻已匯入 Zotero，{len(downloaded)} 篇 PDF 已存至 output/{topic_folder}/pdfs/")

    if downloaded:
        if st.button("📂 開啟 PDF 資料夾"):
            subprocess.Popen(["explorer", pdf_dir])

    with st.expander("查看 BibTeX 內容"):
        st.code(bibtex, language="bibtex")

    st.download_button(
        label="⬇️ 下載 .bib 檔案",
        data=bibtex,
        file_name=filename,
        mime="text/plain",
        use_container_width=True,
    )
