# LLM-backed user-input guardrail demo

This demo uses NeMo's `self check input` rail with the existing CPU Qwen3 model.
It is a policy-classification demonstration, not a validated security boundary.
The classifier and the agent share model capacity and may share model errors.
The prompt covers instruction overrides, hidden prompts/credentials, and attempts
to bypass namespace authorization. Educational security questions remain allowed.

## Request path

1. User question -> existing `agent-guardrails` PII and TCKN rails.
2. If PII passes, question -> `agent-policy-guardrails` -> OGX -> MaaS -> Qwen3.
3. NeMo parses BLOCK as denial and ALLOW as permission. Any other decision fails closed.
4. Only an allowed question starts the existing MCP/RAG/agent flow.
5. Existing PII checks on MCP results, RAG sources, and final output still run.

Only the original user question receives the LLM-backed check. MCP/RAG context
and final answers do not receive this new semantic check. This is not protection
against all indirect prompt injection or answer hallucinations.

## Components

- `components/guardrails/llm-input-policy.yaml`: separate ConfigMap and NeMo CR.
- Existing `rhoai-demo-guardrails` Application deploys both NeMo services.
- Existing `rhoai-demo-langgraph-mcp` supplies the new HTTPS service URL.
- `guardrails.py`: adds input_policy after PII and before any agent action.
- The existing namespace-scoped `get services` Role also authorizes this proxy.
- Agent -> NeMo uses the existing service CA and rotating ServiceAccount token.
- NeMo -> OGX reuses the same internal HTTP model API as the current application.
- The SDK API key is a dummy placeholder, not a credential. MaaS credentials
  remain in OGX's existing Secret. No new secret or separately served model is needed.

The policy prompt is held in `prompts.yml` inside the ConfigMap. `config.py`
registers a strict output parser: only ALLOW/BLOCK (optionally a final period and a
complete leading Qwen reasoning block) is accepted. A truncated or malformed
decision raises an error instead of implicitly allowing the request.

## Deploy

After applying and pushing the patch:

1. Sync `rhoai-demo-guardrails` and wait for the new NeMo service to be Ready.
2. Sync `rhoai-demo-langgraph-mcp` and start a build for the changed Python code.

```bash
oc get nemoguardrails -n maas-demo
oc rollout status deployment/agent-policy-guardrails -n maas-demo --timeout=300s
oc start-build langgraph-mcp -n maas-demo --follow --wait
oc rollout status deployment/langgraph-mcp -n maas-demo --timeout=300s
```

New cluster: the first LangGraph build already includes the current code.
Policy ConfigMap changes alone do not require rebuilding the agent.

## Validate before relying on the policy

```bash
oc exec -i deployment/langgraph-mcp -n maas-demo -- python3 - < scripts/guardrails/test_input_policy.py
```

Expected outcomes: normal questions pass, override requests block at
`input_policy`, and PII blocks at `input` before the judge call. These are test
expectations, not pre-verified model decisions. Check normal MCP/RAG/combined
questions and an override request through the web UI after the tests.

MLflow should show `guardrails.input_policy` and an actual LLM call count >= 1.
Raw questions and judge replies are not added to the application's MLflow spans.
Agent model_calls/total_tokens still describe the original agent flow; judge
calls/tokens are recorded separately in the policy span.

Policy denial returns HTTP 422 with `stage: input_policy` and a policy-specific
message. HTTP 503 means a control error, not a successful policy denial.
Inspect HTTP 403, SDK/configuration errors, malformed decisions, timeouts, or
MaaS quotas separately. The proxy currently has a 30-second upstream timeout;
the model request timeout is 20 seconds and the application timeout 35 seconds.
CPU inference or quota exhaustion may therefore stop a request with HTTP 503.

Diagnostics (do not publish raw logs containing user data):

```bash
oc logs deployment/agent-policy-guardrails -n maas-demo -c nemo-guardrails --tail=60
oc get nemoguardrails agent-policy-guardrails -n maas-demo -o yaml
```

## Rollback

Revert this integration commit, push, sync the LangGraph Application, and rebuild
it. The previous PII-only application behavior is restored. Then sync the
Guardrails Application; prune only the removed policy CR/ConfigMap if desired.
The original `agent-guardrails` PII service is retained throughout.


## Prompt sensitivity regression

The initial single-message Yes/No prompt rejected a legitimate Turkish pod
summary request without the word "kısaca", while allowing the longer variant.
The updated task uses separate system/user messages and ALLOW/BLOCK labels.
Examples are semantic guidance to the LLM, not an application-side allowlist.
The exact failing web question is retained in the regression test alongside
other benign and prohibited requests. Local parser/configuration tests do not
establish the real model's classification accuracy; rerun cluster and UI tests.
Only the policy ConfigMap and the test script change, so no agent build is needed.
