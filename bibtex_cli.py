"""CLI for BibTeX generator — invoked by Claude Code /bibtex skill."""

import sys
import os
import re
import json
import argparse
import requests
import subprocess
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

sys.stdout.reconfigure(encoding="utf-8")

ZOTERO_EXE = r"C:\Program Files\Zotero\zotero.exe"
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "")
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "")
ZOTERO_DEFAULT_COLLECTION = "8C9KFCNW"  # Quant Trading > 論文 Papers 子夾 collection
BASE_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
PAPERS_BASE_DIR = r"C:\dev\Zotero-Library\Papers"
EMAIL = "bensonlai94531@gmail.com"
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
        translated = r.json()["choices"][0]["message"]["content"].strip()
        return translated
    except Exception:
        return text

JOURNAL_GROUPS = {
    "不限": [],
    "自然科學頂刊": ["Nature", "Science", "Cell"],
    "AI / ML 期刊": ["Nature Machine Intelligence", "Journal of Machine Learning Research",
                    "IEEE Transactions on Pattern Analysis and Machine Intelligence"],
    "醫學頂刊": ["New England Journal of Medicine", "The Lancet", "JAMA", "BMJ"],
    "綜合開放取用": ["PLOS ONE", "Scientific Reports", "Nature Communications"],
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


def download_pdf(work, pdf_dir):
    title = work.get("title") or "Untitled"
    doi_url = work.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "")
    oa = work.get("open_access") or {}
    oa_raw = oa.get("oa_url") or (work.get("primary_location") or {}).get("pdf_url")
    pdf_url = resolve_pdf_url(get_unpaywall_pdf(doi) or oa_raw)
    if not pdf_url:
        return False, "🔍 找不到免費 PDF 連結"
    safe = re.sub(r'[\\/:*?"<>|]', "_", title[:100]) + ".pdf"
    try:
        r = requests.get(pdf_url, timeout=20, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"})
        if r.status_code == 200 and b"%PDF" in r.content[:10]:
            with open(os.path.join(pdf_dir, safe), "wb") as f:
                f.write(r.content)
            return True, "已下載"
        elif r.status_code == 403:
            return False, "🔒 伺服器擋掉自動下載，請點連結手動下載"
        elif r.status_code == 404:
            return False, "💔 連結已失效"
        elif r.status_code == 200:
            return False, "🌐 需要瀏覽器開啟，請點連結手動下載"
        else:
            return False, f"❌ 伺服器回應 {r.status_code}"
    except requests.exceptions.Timeout:
        return False, "⏱️ 伺服器回應超時"
    except Exception as e:
        return False, f"❌ 下載失敗（{type(e).__name__}）"


def build_zotero_item(work, collection_key=None):
    authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])]
    year = str(work.get("publication_year") or "")
    title = work.get("title") or "Untitled"
    loc = work.get("primary_location") or {}
    journal = (loc.get("source") or {}).get("display_name", "")
    doi_url = work.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "")
    landing = loc.get("landing_page_url") or ""
    oa_url = (work.get("open_access") or {}).get("oa_url") or ""
    url = doi_url or landing or oa_url
    work_type = work.get("type", "")

    type_map = {
        "article": "journalArticle",
        "proceedings-article": "conferencePaper",
        "book": "book",
        "book-chapter": "bookSection",
    }
    item_type = type_map.get(work_type, "journalArticle")

    creators = []
    for name in authors:
        parts = name.strip().rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
        else:
            creators.append({"creatorType": "author", "firstName": "", "lastName": name})

    item = {
        "itemType": item_type,
        "title": title,
        "creators": creators,
        "date": year,
        "url": url,
        "collections": [collection_key] if collection_key else [],
        "tags": [],
        "relations": {},
    }
    if journal:
        item["publicationTitle"] = journal
    if doi:
        item["DOI"] = doi
    return item


def import_to_zotero(works, pdf_dir, collection_key, api_key, user_id):
    try:
        from pyzotero import zotero as pyzotero_lib
    except ImportError:
        print("⚠️  pyzotero 未安裝，跳過 Zotero 匯入")
        return

    zot = pyzotero_lib.Zotero(user_id, "user", api_key)
    print("\n📚 匯入 Zotero...")
    imported = 0

    for w in works:
        short_title = (w.get("title") or "Untitled")[:60]
        try:
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            if doi:
                existing = zot.items(q=doi, qmode="everything", limit=5)
                if any(item["data"].get("DOI", "").lower() == doi.lower() for item in existing):
                    print(f"  ⚠️ 已存在，跳過：{short_title}")
                    imported += 1
                    continue

            result = zot.create_items([build_zotero_item(w, collection_key)])
            if not result.get("success"):
                print(f"  ❌ 匯入失敗：{short_title}")
                continue

            item_key = result["success"]["0"]

            safe = re.sub(r'[\\/:*?"<>|]', "_", (w.get("title") or "Untitled")[:100]) + ".pdf"
            pdf_path = os.path.join(pdf_dir, safe)
            if os.path.exists(pdf_path):
                zot.create_items([{
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "title": os.path.basename(pdf_path),
                    "path": pdf_path,
                    "contentType": "application/pdf",
                    "parentItem": item_key,
                    "collections": [],
                    "tags": [],
                    "relations": {},
                }])
                print(f"  ✅ {short_title}（含 PDF）")
            else:
                print(f"  ✅ {short_title}")
            imported += 1

        except Exception as e:
            print(f"  ❌ {short_title}：{e}")

    print(f"📚 已匯入 {imported}/{len(works)} 篇到 Zotero（Collection: {collection_key}）")


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
    <tbody>{"".join(rows)}</tbody>
  </table>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--sort", default="引用次數")
    parser.add_argument("--lang", default="不限")
    parser.add_argument("--journal", default="不限")
    parser.add_argument("--institution", default="不限")
    parser.add_argument("--min-citations", type=int, default=10)
    parser.add_argument("--only-oa", action="store_true", default=True)
    parser.add_argument("--zotero-collection", default=ZOTERO_DEFAULT_COLLECTION)
    parser.add_argument("--phrase", default="", help="必須出現在標題的核心片語（用引號做 exact match）")
    args = parser.parse_args()

    search_topic = args.topic
    if has_chinese(args.topic):
        print(f"🌐 偵測到中文，正在翻譯...")
        search_topic = translate_to_english(args.topic)
        print(f"📝 翻譯結果：{search_topic}")

    print(f"🔍 搜尋主題：{search_topic}")

    # 建立資料夾（用原始中文主題命名，搜尋用英文）
    folder = re.sub(r'[\\/:*?"<>|]', "_", args.topic.strip())[:50]
    out_dir = os.path.join(BASE_OUTPUT_DIR, folder)
    pdf_dir = os.path.join(PAPERS_BASE_DIR, folder)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    # 搜尋參數
    sort_param = "cited_by_count:desc" if args.sort == "引用次數" else "publication_date:desc"
    lang_map = {"英文": "en", "中文": "zh"}

    def build_params(use_phrase: bool):
        p = {
            "per-page": args.count,
            "sort": sort_param,
            "mailto": EMAIL,
            "select": "title,authorships,publication_year,primary_location,doi,type,cited_by_count,open_access,ids",
        }
        if use_phrase and args.phrase:
            # Phrase search：標題必須包含該片語（exact match），再用主題做 abstract 輔助
            f = [f'title.search:"{args.phrase}"']
        else:
            f = [f"title.search:{search_topic}"]
        if args.lang in lang_map:
            f.append(f"language:{lang_map[args.lang]}")
        if args.only_oa:
            f.append("is_oa:true")
        if args.journal != "不限":
            ids = fetch_ids("sources", JOURNAL_GROUPS[args.journal])
            if ids: f.append("primary_location.source.id:" + "|".join(ids))
        if args.institution != "不限":
            ids = fetch_ids("institutions", INSTITUTION_GROUPS[args.institution])
            if ids: f.append("authorships.institutions.id:" + "|".join(ids))
        if args.min_citations > 0:
            f.append(f"cited_by_count:>{args.min_citations}")
        p["filter"] = ",".join(f)
        return p

    # 搜尋（有 phrase 時先用 exact match；若 0 結果自動 fallback 到一般搜尋）
    params = build_params(use_phrase=bool(args.phrase))
    resp = requests.get("https://api.openalex.org/works", params=params, timeout=20)
    resp.raise_for_status()
    raw = resp.json().get("results", [])

    if not raw and args.phrase:
        print(f"⚠️  Phrase search 找不到結果，改用一般搜尋...")
        params = build_params(use_phrase=False)
        resp = requests.get("https://api.openalex.org/works", params=params, timeout=20)
        resp.raise_for_status()
        raw = resp.json().get("results", [])

    # 去重
    seen, works = set(), []
    for w in raw:
        key = w.get("doi") or (w.get("title") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            works.append(w)

    if not works:
        print("❌ 找不到相關文獻，請調整搜尋條件。")
        sys.exit(1)

    print(f"✅ 找到 {len(works)} 篇論文")

    # 儲存 BibTeX
    bibtex = "\n\n".join(to_bibtex(w) for w in works)
    bib_path = os.path.join(out_dir, f"{folder}.bib")
    with open(bib_path, "w", encoding="utf-8") as f:
        f.write(bibtex)
    print(f"📄 BibTeX 已儲存：output/{folder}/{folder}.bib")

    # 儲存 sources.html
    html = build_sources_html(works, args.topic)
    with open(os.path.join(out_dir, "sources.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"🌐 來源清單已儲存：output/{folder}/sources.html")

    # 下載 PDF（先下載，才能附加到 Zotero）
    print("⬇️  開始下載 PDF...")
    ok = 0
    results_data = []
    for w in works:
        title = (w.get("title") or "Untitled")
        authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]
        author_last = re.sub(r"[^a-zA-Z]", "", authors[0].split()[-1]) if authors else "unknown"
        year = str(w.get("publication_year") or "")
        safe = re.sub(r'[\\/:*?"<>|]', "_", title[:100]) + ".pdf"
        pdf_path = os.path.join(pdf_dir, safe)
        success, msg = download_pdf(w, pdf_dir)
        if success:
            ok += 1
            print(f"  ✅ {title[:55]}")
        else:
            print(f"  ❌ {msg}　{title[:55]}")
        results_data.append({
            "index": len(results_data) + 1,
            "title": title,
            "author_last": author_last,
            "year": year,
            "citations": w.get("cited_by_count", 0),
            "pdf_path": pdf_path if success else "",
            "downloaded": success,
        })
    print(f"\n🎉 {ok}/{len(works)} 篇 PDF 下載成功")

    # 輸出 results.json 供後續流程使用
    results_json = {
        "folder": folder,
        "pdf_dir": pdf_dir,
        "papers": results_data,
    }
    results_path = os.path.join(out_dir, "results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2)
    print(f"📋 results.json 已儲存：{results_path}")

    # 匯入 Zotero
    if ZOTERO_API_KEY and ZOTERO_USER_ID:
        import_to_zotero(works, pdf_dir, args.zotero_collection, ZOTERO_API_KEY, ZOTERO_USER_ID)
    elif os.path.exists(ZOTERO_EXE):
        subprocess.Popen([ZOTERO_EXE, "-file", bib_path])
        print("📚 已開啟 Zotero（請手動確認匯入）")

    print(f"\n📁 所有檔案位於：output/{folder}/")


if __name__ == "__main__":
    main()
