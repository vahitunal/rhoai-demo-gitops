#!/usr/bin/env python3
"""Reset only demo-docs and the three named demo files after PVC is mounted."""
import subprocess

REMOTE_CODE = r'''
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import yaml

BASE = "http://127.0.0.1:8321/v1"
NAMES = {"pod-erisim-notu.txt", "maas-erisim-notu.txt", "rag-altyapi-notu.txt"}

def call(path, method="GET"):
    request = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    return json.loads(data) if data else None

def segment(value):
    return urllib.parse.quote(value, safe="")

def list_all(path):
    rows, seen = [], set()
    next_path = path + "?limit=100"
    while True:
        page = call(next_path)
        batch = page.get("data", [])
        rows.extend(batch)
        if not page.get("has_more"):
            return rows
        cursor = batch[-1]["id"] if batch else None
        if not cursor or cursor in seen:
            raise RuntimeError("API sayfalaması ilerlemiyor.")
        seen.add(cursor)
        next_path = path + "?limit=100&after=" + segment(cursor)

def reset():
    with open("/etc/ogx/config.yaml") as file:
        config = yaml.safe_load(file)
    paths = [p.get("config", {}).get("storage_dir") for p in config.get("providers", {}).get("files", []) if p.get("provider_type") == "inline::localfs"]
    if paths != ["/.ogx/files"] or not (os.path.ismount("/.ogx") or os.path.ismount("/.ogx/files")):
        raise RuntimeError("/.ogx/files ayrı bir volume olarak bağlı değil; hiçbir veri silinmedi.")

    stores = [row for row in list_all("/vector_stores") if row.get("name") == "demo-docs"]
    if len(stores) > 1:
        raise RuntimeError("demo-docs adında birden fazla store var; hiçbir veri silinmedi.")
    files = list_all("/files")
    attached_ids = set()
    if stores:
        store_id = stores[0]["id"]
        attached_ids = {r["id"] for r in list_all("/vector_stores/" + segment(store_id) + "/files")}
        known = {r["id"] for r in files if r.get("filename") in NAMES}
        # The original missing file can already have been removed from the Files API
        # by the prior GET /content. Its exact ID was observed in this demo.
        known.add("file-628eb15f3cec4283b394d0bcd7e60648")
        unknown = attached_ids - known
        if unknown:
            raise RuntimeError("demo-docs içinde bu pakete ait olmayan dosyalar var; hiçbir veri silinmedi: " + str(sorted(unknown)))

    # Do not remove named Files objects shared with a different vector store.
    targets = {r["id"] for r in files if r.get("filename") in NAMES}
    for other in list_all("/vector_stores"):
        if other.get("name") == "demo-docs":
            continue
        ids = {r["id"] for r in list_all("/vector_stores/" + segment(other["id"]) + "/files")}
        if ids & targets:
            raise RuntimeError("Demo dosyası başka store tarafından da kullanılıyor; hiçbir veri silinmedi.")

    if stores:
        call("/vector_stores/" + segment(stores[0]["id"]), "DELETE")
        print("SIFIRLANDI demo-docs:", stores[0]["id"], flush=True)
    else:
        print("demo-docs zaten yok; dosya kayıtları kontrol ediliyor.", flush=True)
    for file in files:
        if file.get("filename") not in NAMES:
            continue
        try:
            call("/files/" + segment(file["id"]), "DELETE")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        print("TEMIZLENDI", file["filename"], file["id"], flush=True)
    print("Şimdi python3 scripts/rag/load_demo_docs.py çalıştır.", flush=True)

try:
    reset()
except urllib.error.HTTPError as error:
    print("HTTP:", error.code, error.read().decode(), flush=True)
    raise SystemExit(1)
except RuntimeError as error:
    print("Hata:", error, flush=True)
    raise SystemExit(1)
'''

def main():
    result = subprocess.run(
        ["oc", "exec", "-i", "-n", "maas-demo", "deployment/ogx-agent-demo", "--", "python3", "-c", REMOTE_CODE],
        check=False
    )
    raise SystemExit(result.returncode)

if __name__ == "__main__":
    main()
