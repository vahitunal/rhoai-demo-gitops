# Agent PII guardrails

NeMo Guardrails must be Ready as `agent-guardrails` in `maas-demo`.
The agent calls `https://agent-guardrails.maas-demo.svc/v1/guardrail/checks`.
TLS verification uses the existing service CA mount. The existing projected
ServiceAccount token is read for each check so token rotation is supported.
A namespace Role grants only `get` on the `agent-guardrails` Service, matching
the resource authorization performed by kube-rbac-proxy.

All three entry points (pods, rag, combined) check the prompt before invoking
MCP, retrieval, or the LLM. Retrieved sources and MCP text are checked before
being supplied to the model. The entire response (including source snippets
and tool results) is checked with output rails before it is returned to the UI.
Detected PII returns HTTP 422; unavailable or invalid guardrail results return
HTTP 503. Only explicit success from the expected PII rail allows continuation.

MLflow manual spans remain enabled. LangChain autolog and automatic root
argument capture are disabled to prevent unchecked content from being exported.
Spans record check outcomes, duration, character/source counts, model call and
token counts; raw questions, retrieved documents, tool bodies, and model answers
are no longer explicitly captured. Existing historical traces are unchanged.

## Deploy

After applying the integration patch, inspect and push the changes:

```bash
git diff --check
oc apply --dry-run=server -k components/langgraph-mcp
git add src/langgraph-mcp/guardrails.py src/langgraph-mcp/app.py \
  src/langgraph-mcp/web.py components/langgraph-mcp/server.yaml docs/guardrails.md
git commit -m "Enforce PII guardrails across MCP and RAG agent flows"
git push origin main
```

Sync `rhoai-demo-langgraph-mcp` in GitOps. Verify the actual Application name
with `oc get applications -n openshift-gitops` if it was customized.
BuildConfig has only a ConfigChange trigger: code changes require a build.

```bash
oc start-build langgraph-mcp -n maas-demo --follow
oc rollout status deployment/langgraph-mcp -n maas-demo --timeout=300s
oc auth can-i get services/agent-guardrails -n maas-demo \
  --as=system:serviceaccount:maas-demo:langgraph-mcp
```

The authorization command should return `yes`. If a live request returns 403,
inspect `agent-guardrails-rbac-proxy-config` in `maas-demo` and compare its
`authorization.resourceAttributes` with the new Role. Do not widen the Role
unless the actual proxy configuration requires it.

## Verify application enforcement

Use synthetic data only. An e-mail prompt must return HTTP 422 in each mode
with `guardrails.status=blocked` and `guardrails.stage=input`.

```bash
APP_URL="https://$(oc get route langgraph-mcp -n maas-demo -o jsonpath='{.spec.host}')"
for mode in pods rag combined; do
  curl -sS --max-time 60 -w '\nHTTP %{http_code}\n' \
    "$APP_URL/api/chat" -H 'Content-Type: application/json' \
    -d "{\"mode\":\"$mode\",\"prompt\":\"E-posta adresim test@example.com\"}"
done
```

Test a clean prompt in each UI mode and inspect the new MLflow traces. Synthetic
output rejection can be tested inside the app container without an LLM call:

```bash
oc exec -i deployment/langgraph-mcp -n maas-demo -- python3 - <<'PY'
import asyncio
from guardrails import guarded_agent, GuardrailBlocked

@guarded_agent("guardrails_output_smoke")
async def synthetic_agent(prompt):
    return {"answer": "İletişim adresi test@example.com", "model_calls": 0}

try:
    asyncio.run(synthetic_agent("Merhaba"))
except GuardrailBlocked as error:
    assert error.stage == "output", error.stage
    print("PASS: synthetic output blocked")
else:
    raise SystemExit("FAIL: synthetic output was returned")
PY
```

This is a minimal demo policy, not complete Turkish PII coverage. Validate
telephone and credit-card recognition with synthetic examples separately.
Turkish person names and T.C. identity-number checks are not configured.
PII detection may produce false positives and false negatives. Regex/Presidio
PII rails do not provide prompt-injection or hallucination protection.

## Rollback

Revert the integration commit and push; sync the application and rebuild it.
This restores the previous agent behavior, including its previous tracing
configuration. Guardrails resources in `components/guardrails` remain separate.
