#!/usr/bin/env python3
"""Patch the repository OGX manifest using PyYAML already installed in OGX."""
from pathlib import Path
import subprocess
import tempfile

PATCH_CODE = r'''
import sys
import yaml

documents = list(yaml.safe_load_all(sys.stdin.read()))
servers = [d for d in documents if d and d.get("kind") == "OGXServer" and d["metadata"]["name"] == "ogx-agent-demo"]
configs = [d for d in documents if d and d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "ogx-agent-demo-config"]
if len(servers) != 1 or len(configs) != 1:
    raise RuntimeError("Beklenen OGXServer ve ConfigMap bulunamadı.")
config = yaml.safe_load(configs[0]["data"]["config.yaml"])
providers = config.get("providers", {}).get("files", [])
if not any(p.get("provider_type") == "inline::localfs" and p.get("config", {}).get("storage_dir") == "/.ogx/files" for p in providers):
    raise RuntimeError("Files storage_dir /.ogx/files değil; otomatik değişiklik durduruldu.")

workload = servers[0]["spec"].setdefault("workload", {})
storage = workload.setdefault("storage", {})
storage.setdefault("size", "2Gi")
storage.setdefault("mountPath", "/.ogx")
overrides = workload.setdefault("overrides", {})
desired_volume = {
    "name": "rag-files-persistent",
    "persistentVolumeClaim": {"claimName": "ogx-agent-demo-pvc"}
}
desired_mount = {"name": "rag-files-persistent", "mountPath": "/.ogx/files"}

volumes = overrides.setdefault("volumes", [])
existing = [v for v in volumes if v.get("name") == desired_volume["name"]]
if existing and existing != [desired_volume]:
    raise RuntimeError("rag-files-persistent volume tanımı farklı; işlem durduruldu.")
if not existing:
    volumes.append(desired_volume)
mounts = overrides.setdefault("volumeMounts", [])
existing = [m for m in mounts if m.get("mountPath") == desired_mount["mountPath"] or m.get("name") == desired_mount["name"]]
if existing and existing != [desired_mount]:
    raise RuntimeError("Files mount tanımı farklı; işlem durduruldu.")
if not existing:
    mounts.append(desired_mount)

class Dumper(yaml.SafeDumper):
    pass
def represent_string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)
Dumper.add_representer(str, represent_string)
sys.stdout.write(yaml.dump_all(documents, Dumper=Dumper, sort_keys=False, allow_unicode=True))
'''

def main():
    root = Path(__file__).resolve().parents[2]
    manifest = root / "components/ogx-agent/ogx-agent-demo.yaml"
    original = manifest.read_text(encoding="utf-8")
    result = subprocess.run(
        ["oc", "exec", "-i", "-n", "maas-demo", "deployment/ogx-agent-demo", "--",
         "python3", "-c", PATCH_CODE],
        input=original, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise SystemExit(result.stderr or "Manifest güncellenemedi.")
    if not result.stdout.startswith("apiVersion:"):
        raise SystemExit("Beklenen YAML çıktısı alınamadı; dosya değiştirilmedi.")
    with tempfile.NamedTemporaryFile(mode="w", prefix="ogx-before-pvc-", suffix=".yaml", delete=False) as backup:
        backup.write(original)
        print("Önceki dosya yedeği:", backup.name)
    manifest.write_text(result.stdout, encoding="utf-8")
    print("Güncellendi:", manifest)
    print("PVC: ogx-agent-demo-pvc → /.ogx/files")
    print("Git commit/push ve GitOps sync sonrası rollout kontrolü yapılmalı.")

if __name__ == "__main__":
    main()
