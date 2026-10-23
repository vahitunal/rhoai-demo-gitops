import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

mcp = FastMCP(
    "maas-demo-pods",
    host="0.0.0.0",
    port=8080,
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def list_pods(namespace: Literal["maas-demo"]) -> dict:
    """List current pod names, phases, readiness and restarts in maas-demo."""
    if namespace != "maas-demo":
        raise ValueError("Yalnızca maas-demo namespace sorgulanabilir.")

    token = (SA_DIR / "token").read_text().strip()
    context = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))

    request = urllib.request.Request(
        "https://kubernetes.default.svc/api/v1/namespaces/maas-demo/pods",
        headers={"Authorization": "Bearer " + token},
    )

    try:
        with urllib.request.urlopen(
            request, context=context, timeout=20
        ) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Kubernetes API HTTP {error.code}"
        ) from None
    except urllib.error.URLError:
        raise RuntimeError("Kubernetes API bağlantısı başarısız.") from None

    pods = []
    for pod in data.get("items", []):
        status = pod.get("status", {})
        containers = status.get("containerStatuses", [])
        total = len(pod.get("spec", {}).get("containers", []))

        pods.append({
            "name": pod["metadata"]["name"],
            "phase": status.get("phase", "Unknown"),
            "ready": f"{sum(c.get('ready', False) for c in containers)}/{total}",
            "restarts": sum(c.get("restartCount", 0) for c in containers),
            "waiting_reasons": [
                c["state"]["waiting"].get("reason", "Unknown")
                for c in containers
                if "waiting" in c.get("state", {})
            ],
        })

    running = [p for p in pods if p["phase"] == "Running"]
    summary = {
        "total_pods": len(pods),
        "running_pods": len(running),
        "running_ready_pods": sum(
            p["ready"].split("/")[0] == p["ready"].split("/")[1]
            for p in running
        ),
        "succeeded_pods": sum(p["phase"] == "Succeeded" for p in pods),
        "pending_pods": sum(p["phase"] == "Pending" for p in pods),
        "failed_pods": sum(p["phase"] == "Failed" for p in pods),
        "other_phase_pods": sum(
            p["phase"] not in {"Running", "Succeeded", "Pending", "Failed"}
            for p in pods
        ),
        "pods_with_waiting_containers": sum(
            bool(p["waiting_reasons"]) for p in pods
        ),
        "pods_with_restarts": sum(p["restarts"] > 0 for p in pods),
        "total_restarts": sum(p["restarts"] for p in pods),
    }
    return {"namespace": namespace, "summary": summary, "pods": pods}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
