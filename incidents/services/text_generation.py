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
    if not _logs_meaningful(log_text or ""):
        err = {"error": "No logs provided for analysis"}
        return {"raw": json.dumps(err, ensure_ascii=False), "model": None}

    api_key = getattr(settings, "GROQ_API_KEY", None)
    last_error = None

    # Agar API key hai, toh pehle Groq (Cloud) try karo
    if api_key:
        primary_model = getattr(settings, "GROQ_MODEL", "llama-3.1-8b-instant")
        fallback_models = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]

        user_message = (
            "Analyze the system logs below and provide a complete, production-grade incident analysis.\n\n"
            "IMPORTANT RULES:\n"
            "1. Identify the TRUE root cause. The 'primary_root_cause' MUST match the FIRST failure in the 'failure_chain'.\n"
            "2. Infrastructure first: If an infrastructure failure (Kubernetes, Docker, Network, Disk) exists, it should be the FIRST link in the failure chain.\n"
            "3. Technical Depth & Specificity (CRITICAL):\n"
            "   - DO NOT use generic phrases like 'inefficient processing', 'load issue', or 'connection problem'.\n"
            "   - Use EXACT technical reasons:\n"
            "     * Memory: Specify 'Memory Leak', 'High Load Spikes', 'Unbounded Queue Growth', or 'GC Thrashing (High Pressure)'.\n"
            "     * API: Specify 'DNS Resolution Failure', 'TCP Connection Refused', 'Request Timeout', or 'SSL/TLS Handshake Error'.\n"
            "     * Database: Specify 'Slow Query Execution', 'Connection Pool Exhaustion', 'Deadlock', or 'Transaction Log Overflow'.\n"
            "4. Causal Link Strength: Explain how one failure triggered another. For example: 'Kubernetes Pod Crash (Infra) triggered an API timeout BECAUSE the load balancer could not route requests to a healthy pod, causing a cascading failure in the Payment Service'.\n"
            "5. Holistic Analysis: Fully explain failures across all layers: Memory (OOM, GC), API, and Infrastructure.\n"
            "6. Prioritize CRITICAL/FATAL logs over ERROR logs.\n"
            "7. Think like a real production engineer, not a basic log parser.\n\n"
            "----------------------------------------\n"
            "ANALYSIS REQUIREMENTS\n"
            "----------------------------------------\n"
            "1. Identify:\n"
            "- Primary Root Cause: The ultimate first trigger in the chain.\n"
            "- Detailed 'Why' Analysis: For each failure, explain the technical reason (e.g., 'DB timeout occurred BECAUSE connection pool was exhausted by Service A').\n"
            "- Secondary Causes (sub_causes): All other detected anomalies.\n\n"
            "2. Detect failure chain:\n"
            "Explain how one failure led to another starting from the infrastructure or first signal.\n"
            "Example: OOMKill (Infra) → Auth Service Failure → DB timeout → Payment Failure\n\n"
            "3. Classify issues into:\n"
            "- Authentication / Database / Infrastructure / API / Memory / Performance\n\n"
            "----------------------------------------\n"
            "REMEDIATION REQUIREMENTS\n"
            "----------------------------------------\n"
            "Provide resolutions in PRIORITY ORDER (P0: Critical/Immediate, P1: Tactical, P2: Long-term).\n"
            "1. Immediate Fix (urgent steps to restore system)\n"
            "2. Root Fix (long-term engineering fix)\n"
            "3. Prevention (how to avoid in future)\n\n"
            "COMMAND SAFETY RULES:\n"
            "- DO NOT suggest scaling down deployments or pods under load (this is risky).\n"
            "- PREFER suggesting Horizontal Pod Autoscaling (HPA), resource limit increases, or monitoring alerts.\n"
            "- ONLY provide safe and production-friendly commands.\n\n"
            "----------------------------------------\n"
            "CONFIDENCE LOGIC (VERY IMPORTANT)\n"
            "----------------------------------------\n"
            "Set confidence based on evidence:\n"
            "- If logs contain multiple CRITICAL/FATAL errors → confidence = HIGH (80-95%)\n"
            "- If logs contain multiple ERRORs but limited CRITICAL → confidence = MEDIUM (60-80%)\n"
            "- If logs are incomplete → confidence = LOW (<60%)\n\n"
            "Also explain WHY confidence is high/medium/low.\n\n"
            "----------------------------------------\n"
            "OUTPUT FORMAT (STRICT JSON)\n"
            "----------------------------------------\n"
            "Return ONLY valid JSON matching this schema exactly:\n"
            "{\n"
            '  "primary_root_cause": "",\n'
            '  "root_cause_depth": [{"component": "", "failure": "", "why": ""}],\n'
            '  "sub_causes": [],\n'
            '  "failure_chain": "",\n'
            '  "severity": "low/medium/high/critical",\n'
            '  "confidence": {\n'
            '    "score": 0,\n'
            '    "level": "low/medium/high",\n'
            '    "reason": ""\n'
            '  },\n'
            '  "analysis_summary": "",\n'
            '  "prioritized_resolutions": {\n'
            '    "P0_immediate": [],\n'
            '    "P1_tactical": [],\n'
            '    "P2_prevention": []\n'
            '  },\n'
            '  "safe_commands": []\n'
            "}\n\n"
            "----------------------------------------\n"
            "INPUT LOGS:\n"
            "----------------------------------------\n"
            f"{log_text}"
        )

        client = Groq(api_key=api_key)
        models_to_try = [primary_model] + [m for m in fallback_models if m != primary_model]

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
                if "model_decommissioned" in last_error or "decommissioned" in last_error:
                    continue
                break  # Agar API block hai, direct bahar aake Local Model pe jao
    else:
        print("⚠️ No Groq API Key found. Skipping cloud models.")

    if not getattr(settings, "ENABLE_LOCAL_AI_FALLBACK", False):
        return {"raw": "{}", "error": last_error or "Groq request failed and local fallback is disabled"}

    # 2. LOCAL FALLBACK LOGIC START (Agar Groq fail ho ya API key na ho)
    try:
        from .local_inference import LocalPhi3Fallback

        local_result = LocalPhi3Fallback.analyze(log_text)
        if local_result:
            return {
                "raw": json.dumps(local_result, ensure_ascii=False),
                "model": "local_phi3_sre_fallback"
            }
        else:
            last_error = "Local model returned empty result."
    except Exception as e:
        last_error = f"Groq failed and local fallback also failed: {str(e)}"

    # Agar donu (Groq + Local) fail ho jaye
    return {"raw": "{}", "error": last_error or "All AI requests failed"}
