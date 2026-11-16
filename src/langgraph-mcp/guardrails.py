"""Fail-closed PII checks; this module never logs checked content or tokens."""
import json
import os
import ssl
from functools import wraps
from pathlib import Path
from time import monotonic

import httpx
import mlflow


class GuardrailBlocked(RuntimeError):
    def __init__(self, stage):
        self.stage = stage
        super().__init__("Kişisel veri tespit edildi; içerik engellendi.")


class GuardrailUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Kişisel veri kontrolü tamamlanamadı; istek durduruldu.")


async def check_text(text, stage, role="user"):
    """Only explicit success from the configured rail permits continuation."""
    started = monotonic()
    with mlflow.start_span(name="guardrails." + stage, span_type="GUARDRAIL") as span:
        span.set_inputs({"stage": stage, "characters": len(text)})
        try:
            if not text or len(text) > 262144:
                raise GuardrailUnavailable()
            base = os.environ["GUARDRAILS_URL"].rstrip("/")
            if not base.startswith("https://"):
                raise GuardrailUnavailable()
            ca_path = os.environ["GUARDRAILS_CA_PATH"]
            token = Path(os.environ.get(
                "GUARDRAILS_TOKEN_PATH",
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            )).read_text().strip()
            if not token:
                raise GuardrailUnavailable()
            async with httpx.AsyncClient(
                verify=ssl.create_default_context(cafile=ca_path),
                trust_env=False, timeout=httpx.Timeout(25, connect=5),
            ) as client:
                response = await client.post(
                    base + "/v1/guardrail/checks",
                    headers={"Authorization": "Bearer " + token},
                    json={"model": "pii-check", "messages": [
                        {"role": role, "content": text}
                    ]},
                )
                response.raise_for_status()
                data = response.json()
            direction = "output" if role == "assistant" else "input"
            expected_rails = (
                "detect sensitive data on " + direction,
                "regex check " + direction,
            )
            rails = data.get("rails_status", {})
            statuses = {
                name: rails.get(name, {}).get("status") for name in expected_rails
            }
            blocked_rails = [name for name, value in statuses.items() if value == "blocked"]
            status = data.get("status")
            # A blocking rail may short-circuit subsequent rails.
            if status == "blocked" and blocked_rails:
                span.set_outputs({"status": "blocked", "blocked_rails": blocked_rails})
                raise GuardrailBlocked(stage)
            # Success requires both configured rails to explicitly pass.
            if status != "success" or any(value != "success" for value in statuses.values()):
                raise GuardrailUnavailable()
            span.set_outputs({"status": "success"})
        except GuardrailBlocked:
            raise
        except Exception:
            span.set_outputs({"status": "error"})
            raise GuardrailUnavailable() from None
        finally:
            span.set_attribute("duration_ms", round((monotonic() - started) * 1000))


def guarded_agent(name):
    """Create metadata-only root spans without automatic argument capture."""
    def decorate(function):
        @wraps(function)
        async def wrapped(prompt):
            with mlflow.start_span(name=name, span_type="AGENT") as span:
                span.set_inputs({"prompt_characters": len(prompt)})
                await check_text(prompt, "input")
                result = await function(prompt)
                # Check the whole response, including sources and MCP details.
                await check_text(
                    json.dumps(result, ensure_ascii=False), "output", "assistant"
                )
                span.set_outputs({
                    "guardrails_status": "success",
                    "model_calls": result.get("model_calls", 0),
                    "total_tokens": result.get("total_tokens", 0),
                })
                return result
        return wrapped
    return decorate
