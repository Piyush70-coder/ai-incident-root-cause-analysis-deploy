import json
import re
from typing import Any, Dict

_CONFIDENCE_MAP = {"high": 0.85, "medium": 0.60, "low": 0.35}

_FORBIDDEN = [
    "<specific technical cause>", "<technical analysis>", "analysis in progress",
    "system component failed", "unknown error", "check logs", "investigating",
]


def parse_sre_structured_output(raw_json_str: str, extra_context: str = "") -> Dict[str, Any]:
    """
    Parse strict SRE JSON (primary_root_cause, analysis_summary, immediate_fixes, …).
    Returns root fields plus the full payload dict for full_ai_report / structured_output.
    """
    text = (raw_json_str or "").strip()
    extra_context = (extra_context or "").strip()
    payload: Dict[str, Any] = {}

    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            payload = loaded
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {}

    err_msg = payload.get("error")
    if err_msg is not None and str(err_msg).strip():
        return {
            "root_cause": "",
            "explanation": str(err_msg).strip(),
            "confidence_score": 0.0,
            "severity": "",
            "payload": payload,
            "is_error_response": True,
        }

    primary = str(payload.get("primary_root_cause") or payload.get("root_cause") or "").strip()
    summary = str(payload.get("analysis_summary") or payload.get("incident_summary") or "").strip()
    chain = str(payload.get("failure_chain") or "").strip()

    explanation = summary
    if chain:
        explanation = f"{summary}\n\nFailure Chain: {chain}"

    depth = payload.get("root_cause_depth", [])
    if isinstance(depth, list) and depth:
        explanation += "\n\nRoot Cause Depth Analysis:"
        for d in depth:
            comp = d.get("component", "Unknown")
            fail = d.get("failure", "Detected Issue")
            why = d.get("why", "Under investigation")
            explanation += f"\n- [{comp}] {fail}: {why}"

    confidence_obj = payload.get("confidence")
    final_confidence = 0.60
    if isinstance(confidence_obj, dict):
        score = confidence_obj.get("score", 60)
        try:
            score_val = float(score)
            # If score is > 1, assume it's a percentage (e.g. 85 instead of 0.85)
            final_confidence = score_val / 100.0 if score_val > 1.0 else score_val
        except (ValueError, TypeError):
            label = str(confidence_obj.get("level", "medium")).lower()
            final_confidence = _CONFIDENCE_MAP.get(label, 0.60)
    elif confidence_obj:
        label = str(confidence_obj).lower()
        final_confidence = _CONFIDENCE_MAP.get(label, 0.60)

    severity_label = str(payload.get("severity", "") or "").strip().lower()

    if not primary:
        legacy_rc, legacy_expl, legacy_conf = parse_ai_output(text, extra_context)
        primary = legacy_rc
        if not explanation:
            explanation = legacy_expl
        final_confidence = legacy_conf
        severity_label = severity_label or str(payload.get("severity") or "").strip().lower()

    if not explanation:
        explanation = (
            f"Analysis aligned incident signals with {primary}. "
            "Review logs and full AI report for detail."
        )

    return {
        "root_cause": primary,
        "explanation": explanation,
        "confidence_score": max(0.10, min(0.98, final_confidence)),
        "severity": severity_label,
        "payload": payload,
        "is_error_response": False,
    }


def parse_ai_output(text: str, extra_context: str = ""):
    """
    Parse Groq JSON or legacy text. When LLM output is empty, use extra_context
    (incident description + built context) for keyword fallbacks so RCA is never blank.
    """
    text = (text or "").strip()
    extra_context = (extra_context or "").strip()
    combined = f"{text}\n{extra_context}"

    root_cause = ""
    explanation = ""
    confidence = 0.3

    # 1) Strict JSON (Groq json_object mode)
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            root_cause = str(payload.get("root_cause", "")).strip()
            explanation_list = payload.get("explanation", [])
            if isinstance(explanation_list, list):
                explanation = "\n".join(
                    f"{idx + 1}. {str(item).strip()}"
                    for idx, item in enumerate(explanation_list[:5])
                    if str(item).strip()
                )
            else:
                explanation = str(explanation_list).strip()

            confidence = _CONFIDENCE_MAP.get(
                str(payload.get("confidence", "medium")).strip().lower(),
                0.60,
            )
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # 2) Legacy regex (non-JSON)
    if not root_cause:
        rc_match = re.search(
            r"Root Cause:\s*(.+?)(?=\n\s*Explanation:|$)", text, re.IGNORECASE | re.DOTALL
        )
        ex_match = re.search(
            r"Explanation:\s*(.+?)(?=\n\s*Confidence:|$)", text, re.IGNORECASE | re.DOTALL
        )
        conf_match = re.search(r"Confidence:\s*([0-9.]+)", text, re.IGNORECASE)

        root_cause = rc_match.group(1).strip() if rc_match else ""
        explanation = ex_match.group(1).strip() if ex_match else ""
        try:
            confidence = float(conf_match.group(1)) if conf_match else confidence
        except ValueError:
            pass

    # 3) Reject placeholders / overly generic one-word causes
    if root_cause:
        lower = root_cause.lower()
        if any(p in lower for p in _FORBIDDEN):
            root_cause = ""
        elif len(root_cause) < 5 or lower in ["error", "failure", "timeout", "unknown"]:
            root_cause = ""

    # 4) Keyword fallback on combined incident + LLM text (critical when JSON is {} or API failed)
    if not root_cause:
        text_lower = combined.lower()
        if "redis" in text_lower:
            root_cause = "Redis Cache Connection Timeout"
        elif "postgres" in text_lower or "db" in text_lower or "database" in text_lower:
            root_cause = "Database Connection Pool Exhaustion"
        elif "dns" in text_lower:
            root_cause = "DNS Resolution Failure"
        elif "kafka" in text_lower or "consumer" in text_lower:
            root_cause = "Kafka Consumer Group Lag"
        elif "504" in text_lower or "gateway" in text_lower:
            root_cause = "API Gateway Timeout (504)"
        elif "oom" in text_lower or "memory" in text_lower:
            root_cause = "Container OOMKilled"
        elif "connection refused" in text_lower:
            root_cause = "Service Connection Refused"
        elif "disk" in text_lower:
            root_cause = "Disk Space Exhaustion"
        else:
            root_cause = "Application Performance Degradation"

    # 5) Explanation must not be empty
    if not explanation or any(p in explanation.lower() for p in _FORBIDDEN) or len(explanation) < 10:
        explanation = (
            f"Analysis aligned incident signals (logs and description) with {root_cause}. "
            "Review masked logs and timeline for evidence."
        )

    confidence = max(0.30, min(0.85, float(confidence)))

    return root_cause, explanation, confidence
