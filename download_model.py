import json
import re
from django.conf import settings
from groq import Groq

SYSTEM_MESSAGE = (
    "You are a Senior Site Reliability Engineer (SRE) with deep expertise in debugging distributed systems, "
    "microservices, Kubernetes, databases, and APIs. Your task is to analyze system logs and provide a complete, "
    "production-grade incident analysis. Return ONLY valid JSON."
)


def _logs_meaningful(log_text: str) -> bool:
    if log_text is None:
        return False
    s = re.sub(r"\s+", " ", str(log_text).strip())
    if len(s) < 12:
        return False
    if not re.search(r"[A-Za-z0-9]", s):
        return False
    lowered = s.lower()
    if lowered in ("no logs", "[empty]", "n/a", "null"):
        return False
    return True


def generate_root_cause(log_text: str) -> dict:
    api_key = getattr(settings, "GROQ_API_KEY", None)

    if not api_key:
        print("⚠️ GROQ_API_KEY missing in settings!")
        return {"raw": "{}", "error": "Missing GROQ_API_KEY in settings"}

    if not _logs_meaningful(log_text or ""):
        err = {"error": "No logs provided for analysis"}
        return {"raw": json.dumps(err, ensure_ascii=False), "model": None}

    primary_model = getattr(settings, "GROQ_MODEL", "llama-3.1-8b-instant")
    fallback_models = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]

    user_message = (
        "Analyze the system logs below and provide a complete, production-grade incident analysis.\n\n"
        "IMPORTANT RULES:\n"
        "1. Identify the TRUE root cause. The 'primary_root_cause' MUST match the FIRST failure in the 'failure_chain'.\n"
        "2. Return ONLY valid JSON matching the schema provided previously.\n\n"
        "----------------------------------------\n"
        "INPUT LOGS:\n"
        "----------------------------------------\n"
        f"{log_text}"
    )

    client = Groq(api_key=api_key)
    models_to_try = [primary_model] + [m for m in fallback_models if m != primary_model]
    last_error = None

    for model_name in models_to_try:
        try:
            response = client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                messages=[
                    {"role": "system", "content": SYSTEM_MESSAGE},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
            )
            raw = (response.choices[0].message.content or "").strip()
            parsed = json.loads(raw)
            return {"raw": json.dumps(parsed, ensure_ascii=False), "model": model_name}
        except Exception as exc:
            last_error = str(exc)
            print(f"⚠️ Groq Error ({model_name}): {last_error}")
            continue

    return {"raw": "{}", "error": last_error or "Groq API request failed"}