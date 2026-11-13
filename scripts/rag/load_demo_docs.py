#!/usr/bin/env python3
"""Bastion'da çalıştır: python3 scripts/rag/load_demo_docs.py."""
import json
from pathlib import Path
import subprocess
import sys


REMOTE_CODE = r'''
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8321/v1"

def call(path, payload=None, body=None, content_type="application/json", raw=False):
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, data=body, headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
    return data if raw else json.loads(data)

def segment(value):
    return urllib.parse.quote(value, safe="")

def list_all(path):
    rows, seen = [], set()
    next_path = path + "?limit=100"
    while True:
        page = call(next_path)
        current = page.get("data", [])
        rows.extend(current)
        if not page.get("has_more"):
            return rows
        cursor = current[-1]["id"] if current else None
        if not cursor or cursor in seen:
            raise RuntimeError("API sayfalaması ilerlemiyor: " + path)
        seen.add(cursor)
        next_path = path + "?limit=100&after=" + segment(cursor)

def upload(filename, text):
    boundary = "----rag-" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        'assistants\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        'Content-Type: text/plain; charset=utf-8\r\n\r\n'
        f"{text}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    return call("/files", body=body, content_type=f"multipart/form-data; boundary={boundary}")

def wait_completed(store_id, file_id, attached):
    deadline = time.monotonic() + 180
    while attached.get("status") in ("in_progress", "queued"):
        if time.monotonic() >= deadline:
            raise RuntimeError("İndeksleme sürüyor; script yeniden çalıştırılabilir: " + file_id)
        time.sleep(2)
        attached = call(f"/vector_stores/{segment(store_id)}/files/{segment(file_id)}")
    if attached.get("status") != "completed":
        raise RuntimeError("İndeksleme başarısız: " + json.dumps(attached, ensure_ascii=False))
    return attached

def ingest(bundle):
    stores = [row for row in list_all("/vector_stores") if row.get("name") == "demo-docs"]
    if len(stores) > 1:
        raise RuntimeError("demo-docs adında birden fazla store var; işlem durduruldu.")
    store = stores[0] if stores else call("/vector_stores", {"name": "demo-docs"})
    store_id = store["id"]
    print("Vector store:", store_id, flush=True)
    files = list_all("/files")
    attachments = {
        row["id"]: row
        for row in list_all(f"/vector_stores/{segment(store_id)}/files")
    }

    for filename, text in bundle.items():
        candidates = [row for row in files if row.get("filename") == filename]
        matches = []
        for candidate in candidates:
            existing = call("/files/" + segment(candidate["id"]) + "/content", raw=True)
            if existing.decode("utf-8").strip() == text.strip():
                matches.append(candidate)
        if candidates and not matches:
            raise RuntimeError(
                filename + ": aynı adla farklı içerik bulundu. "
                "Mevcut dosya değiştirilmedi; yeni içerik için farklı dosya adı kullan."
            )
        if matches:
            # Prefer an already attached copy; never create another upload for equal content.
            matches.sort(key=lambda row: row["id"] not in attachments)
            file_info = matches[0]
        else:
            file_info = upload(filename, text)
            files.append(file_info)
        file_id = file_info["id"]
        attached = attachments.get(file_id)
        already_completed = attached is not None and attached.get("status") == "completed"
        if attached is None:
            attached = call(
                f"/vector_stores/{segment(store_id)}/files",
                {
                    "file_id": file_id,
                    "chunking_strategy": {
                        "type": "static",
                        "static": {"max_chunk_size_tokens": 100, "chunk_overlap_tokens": 10}
                    }
                }
            )
        attachments[file_id] = wait_completed(store_id, file_id, attached)
        print(("MEVCUT" if already_completed else "TAMAMLANDI"), filename, flush=True)

    store = call("/vector_stores/" + segment(store_id))
    print("Dosya sayıları:", json.dumps(store.get("file_counts", {})), flush=True)
    for query in (
        "Pod sorguları hangi servis üzerinden yapılıyor?",
        "Model erişim yolu hangi bileşenlerden oluşuyor?",
        "Embedding servisi kaç boyutlu vektör üretiyor?"
    ):
        result = call(
            f"/vector_stores/{segment(store_id)}/search",
            {"query": query, "search_mode": "vector", "max_num_results": 3}
        )
        print("\nSoru:", query, flush=True)
        for hit in result.get("data", []):
            print(" ", hit.get("filename"), "skor:", hit.get("score"), flush=True)

if __name__ == "__main__":
    try:
        ingest(json.load(sys.stdin))
    except urllib.error.HTTPError as error:
        print("HTTP:", error.code, error.read().decode(), file=sys.stderr)
        raise SystemExit(1)
    except (RuntimeError, urllib.error.URLError) as error:
        print("Hata:", error, file=sys.stderr)
        raise SystemExit(1)
'''


def main():
    repo = Path(__file__).resolve().parents[2]
    documents = sorted((repo / "docs" / "rag-demo").glob("*.txt"))
    if not documents:
        raise SystemExit("docs/rag-demo altında .txt dosyası bulunamadı.")
    bundle = {path.name: path.read_text(encoding="utf-8") for path in documents}
    print("Yüklenecek/kontrol edilecek dosyalar:", ", ".join(bundle), flush=True)
    # API calls run inside OGX; no route, port-forward or credential transfer needed.
    result = subprocess.run(
        ["oc", "exec", "-i", "-n", "maas-demo", "deployment/ogx-agent-demo",
         "--", "python3", "-c", REMOTE_CODE],
        input=json.dumps(bundle, ensure_ascii=False), text=True, check=False
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
