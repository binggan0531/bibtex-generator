import streamlit as st
import requests
import os
import re
import io
import zipfile

GROQ_API_KEY = os.getenv("GROQ_API_KEY", st.secrets.get("GROQ_API_KEY", "") if hasattr(st, "secrets") else "")
EMAIL = "bensonlai94531@gmail.com"

st.set_page_config(page_title="BibTeX 文獻產生器", page_icon="📚", layout="centered")
st.title("📚 BibTeX 文獻產生器")
st.caption("搜尋真實學術論文，生成 BibTeX 並下載 PDF")
st.info("📡 資料來源：OpenAlex — 2.5 億篇真實論文，結果 100% 真實存在")

JOURNAL_GROUPS = {
    "不限": [],
    "自然科學頂刊": ["Nature", "Science", "Cell"],
    "AI / ML 期刊": ["Nature Machine Intelligence", "Journal of Machine Learning Research",
                    "IEEE Transactions on Pattern Analysis and Machine Intelligence"],
    "醫學頂刊": ["New England Journal of Medicine", "The Lancet", "JAMA", "BMJ"],
}

INSTITUTION_GROUPS = {
    "不限": [],
    "世界頂尖大學": ["Massachusetts Institute of Technology", "Stanford University",
                  "Harvard University", "University of Oxford", "University of Cambridge",
                  "ETH Zurich", "Caltech"],
    "亞洲頂尖大學": ["National University of Singapore", "University of Tokyo",
                  "Tsinghua University", "Peking University", "Seoul National University"],
    "台灣頂尖大學": ["National Taiwan University", "National Tsing Hua University",
                  "National Chiao Tung University", "Academia Sinica"],
}


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


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_ids(endpoint, names):
    ids = []
    for name in names:
        try:
            r = requests.get(f"https://api.openalex.org/{endpoint}",
                             params={"search": name, "per-page": 1, "mailto": EMAIL}, timeout=10)
            results = r.json().get("results", [])
            if results:
                ids.append(results[0]["id"].split("/")[-1])
        except Exception:
            pass
    return ids


def make_citation_key(authors, year, title):
    last = re.sub(r"[^a-zA-Z]", "", authors[0].split()[-1]).lower() if authors else "unknown"
    word = re.sub(r"[^a-zA-Z]", "", title.split()[0]).lower() if title else "paper"
    return f"{last}{year}{word}"


def to_bibtex(work):
    authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])]
    year = work.get("publication_year") or "n.d."
    title = work.get("title") or "Untitled"
    loc = work.get("primary_location") or {}
    journal = (loc.get("source") or {}).get("display_name", "")
    doi_url = work.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "")
    landing = loc.get("landing_page_url") or ""
    oa_url = (work.get("open_access") or {}).get("oa_url") or ""
    url = doi_url or landing or oa_url
    work_type = work.get("type", "")
    key = make_citation_key(authors, year, title)

    entry = "@article" if work_type == "article" else \
            "@inproceedings" if work_type == "proceedings-article" else \
            "@book" if work_type in ("book", "book-chapter") else "@misc"

    lines = [f"{entry}{{{key},",
             f"  author = {{{' and '.join(authors)}}},",
             f"  title = {{{{{title}}}}},",
             f"  year = {{{year}}},"]
    if journal: lines.append(f"  journal = {{{journal}}},")
    if doi:     lines.append(f"  doi = {{{doi}}},")
    if url:     lines.append(f"  url = {{{url}}},")
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
        r = requests.get(f"https://api.unpaywall.org/v2/{doi}",
                         params={"email": EMAIL}, timeout=10)
        if r.status_code == 200:
            best = r.json().get("best_oa_location") or {}
            return best.get("url_for_pdf")
    except Exception:
        pass
    return None


def build_sources_html(works, topic):
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
        rows.append(f"<tr><td>{i}</td><td>{title_html}</td><td>{source}</td><td>{year}</td></tr>")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8"><title>文獻來源：{topic}</title>
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
    <tbody>{"".join(rows)}</tbody>
  </table>
</body></html>"""


# UI
topic = st.text_input("研究主題", placeholder="例如：machine learning in medical imaging（可輸入中文）")

col1, col2, col3 = st.columns(3)
with col1:
    count = st.select_slider("文獻數量", options=[10, 20, 30, 50], value=20)
with col2:
    sort = st.radio("排序方式", ["引用次數", "最新發表"], horizontal=True)
with col3:
    lang = st.radio("語言", ["不限", "英文", "中文"], horizontal=True)

only_oa = st.checkbox("只顯示可免費下載的論文", value=True)
journal_group = st.selectbox("期刊類別", list(JOURNAL_GROUPS.keys()))
institution_group = st.selectbox("機構篩選", list(INSTITUTION_GROUPS.keys()))
min_citations = st.select_slider("最低引用次數", options=[0, 10, 50, 100, 500, 1000], value=10)

if st.button("搜尋並生成", type="primary", disabled=not topic):

    with st.status("步驟 1 / 2　正在搜尋論文...", expanded=True) as status:
        try:
            search_topic = topic
            if has_chinese(topic):
                st.write("🌐 偵測到中文，正在翻譯...")
                search_topic = translate_to_english(topic)
                st.write(f"📝 翻譯結果：**{search_topic}**")

            sort_param = "cited_by_count:desc" if sort == "引用次數" else "publication_date:desc"
            lang_map = {"英文": "en", "中文": "zh"}
            params = {
                "search": search_topic,
                "per-page": count,
                "sort": sort_param,
                "mailto": EMAIL,
                "select": "title,authorships,publication_year,primary_location,doi,type,cited_by_count,open_access",
            }
            filters = []
            if lang in lang_map:
                filters.append(f"language:{lang_map[lang]}")
            if only_oa:
                filters.append("is_oa:true")
            if journal_group != "不限":
                ids = fetch_ids("sources", tuple(JOURNAL_GROUPS[journal_group]))
                if ids: filters.append("primary_location.source.id:" + "|".join(ids))
            if institution_group != "不限":
                ids = fetch_ids("institutions", tuple(INSTITUTION_GROUPS[institution_group]))
                if ids: filters.append("authorships.institutions.id:" + "|".join(ids))
            if min_citations > 0:
                filters.append(f"cited_by_count:>{min_citations}")
            if filters:
                params["filter"] = ",".join(filters)

            resp = requests.get("https://api.openalex.org/works", params=params, timeout=20)
            resp.raise_for_status()
            raw = resp.json().get("results", [])

            seen, works = set(), []
            for w in raw:
                key = w.get("doi") or (w.get("title") or "").strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    works.append(w)

            if not works:
                st.warning("找不到相關文獻，請嘗試調整篩選條件。")
                st.stop()

            bibtex = "\n\n".join(to_bibtex(w) for w in works)
            sources_html = build_sources_html(works, topic)
            filename = re.sub(r'[\\/:*?"<>|]', "_", topic.strip())[:50]

            status.update(label=f"步驟 1 / 2　找到 {len(works)} 篇論文 ✅", state="complete")

            st.subheader("📋 文獻來源清單")
            for i, w in enumerate(works, 1):
                title = w.get("title") or "Untitled"
                loc = w.get("primary_location") or {}
                doi_url = w.get("doi") or ""
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

    with st.status("步驟 2 / 2　正在下載 PDF...", expanded=True) as status:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"}
        zip_buffer = io.BytesIO()
        ok = 0
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for w in works:
                title = w.get("title") or "Untitled"
                doi_url = w.get("doi") or ""
                doi = doi_url.replace("https://doi.org/", "")
                oa = w.get("open_access") or {}
                oa_raw = oa.get("oa_url") or (w.get("primary_location") or {}).get("pdf_url")
                pdf_url = resolve_pdf_url(get_unpaywall_pdf(doi) or oa_raw)
                if not pdf_url:
                    st.write(f"無 PDF　{title[:55]}")
                    continue
                try:
                    r = requests.get(pdf_url, timeout=20, headers=headers, allow_redirects=True)
                    if r.status_code == 200 and b"%PDF" in r.content[:10]:
                        safe = re.sub(r'[\\/:*?"<>|]', "_", title[:80]) + ".pdf"
                        zf.writestr(safe, r.content)
                        ok += 1
                        st.write(f"✅ {title[:55]}")
                    else:
                        st.write(f"❌ 無法取得　{title[:55]}")
                except Exception:
                    st.write(f"❌ 下載失敗　{title[:55]}")

        status.update(label=f"步驟 2 / 2　PDF 下載完成：{ok}/{len(works)} 篇 ✅", state="complete")

    st.success(f"完成！找到 {len(works)} 篇論文，{ok} 篇 PDF 可下載。")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button("⬇️ 下載 BibTeX", data=bibtex,
                           file_name=f"{filename}.bib", mime="text/plain", use_container_width=True)
    with col2:
        st.download_button("⬇️ 下載來源清單", data=sources_html,
                           file_name=f"{filename}_sources.html", mime="text/html", use_container_width=True)
    with col3:
        if ok > 0:
            zip_buffer.seek(0)
            st.download_button("⬇️ 下載 PDF (zip)", data=zip_buffer,
                               file_name=f"{filename}_pdfs.zip", mime="application/zip", use_container_width=True)
        else:
            st.button("⬇️ 下載 PDF (zip)", disabled=True, use_container_width=True)
