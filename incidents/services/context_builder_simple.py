"""
SRE-style structured context builder for incident analysis
Updated to use FAISS/RAG for extracting relevant log lines.
"""
import re
from incidents.services.similarity_service import find_similar_incidents_db, filter_relevant_logs


def detect_infra_signals(logs, description: str) -> dict:
    """Detect infrastructure component signals from logs and description"""
    signals = {
        'database': [], 'redis': [], 'kafka': [], 'api_latency': [], 'timeouts': [], 'configuration': []
    }

    all_text = description.lower() if description else ""
    for log in logs:
        content = (log.processed_content or "").lower()
        all_text += " " + content

    # Database signals
    db_patterns = [
        (r'connection pool.*exhausted', 'pool exhausted'),
        (r'database.*timeout|db.*timeout|mysql.*timeout|postgres.*timeout', 'timeout'),
        (r'connection.*error|db.*error|database.*error', 'connection error'),
        (r'could not obtain connection', 'connection unavailable'),
        (r'psycopg2|mysql|postgresql', 'driver error'),
    ]
    for pattern, signal in db_patterns:
        if re.search(pattern, all_text, re.IGNORECASE):
            signals['database'].append(signal)

    # Redis/Cache signals
    redis_patterns = [
        (r'redis.*timeout|redis.*error', 'redis failure'),
        (r'cache.*miss|cache.*failure', 'cache failure'),
    ]
    for pattern, signal in redis_patterns:
        if re.search(pattern, all_text, re.IGNORECASE):
            signals['redis'].append(signal)

    # API Latency & Timeouts
    if re.search(r'latency|response.*time|slow.*request', all_text, re.IGNORECASE):
        signals['api_latency'].append('high latency detected')

    if re.search(r'timeout|timed.*out', all_text, re.IGNORECASE):
        signals['timeouts'].append('operation timeout')

    # Configuration / Health Check Errors (Added for better accuracy)
    if re.search(r'health.*check.*failed|misconfigured|404.*health', all_text, re.IGNORECASE):
        signals['configuration'].append('health check failure')

    return signals


def extract_user_impact(description: str, severity: str) -> str:
    """Extract user impact from description and severity"""
    if not description:
        return f'Service impact - {severity} severity'

    desc_lower = description.lower()
    if 'outage' in desc_lower or 'down' in desc_lower:
        return 'Service unavailable - users cannot access the system'
    elif 'slow' in desc_lower or 'latency' in desc_lower:
        return 'Service performance degraded'
    return f'Service impact - {severity} severity'


def build_incident_context_with_similarity(incident):
    """
    Build structured SRE-style context using RAG (FAISS)
    """
    parts = []

    # ================= INCIDENT METADATA =================
    parts.append(f"INCIDENT: {incident.title}")

    # [FIX] Description add kiya taaki AI ko poori baat pata chale
    description_text = incident.description if incident.description else "No description provided."
    parts.append(f"DESCRIPTION: {description_text}")

    parts.append(
        f"SEVERITY: {incident.get_severity_display() if hasattr(incident, 'get_severity_display') else incident.severity}")
    parts.append(f"IMPACT: {extract_user_impact(incident.description or '', incident.severity)}")
    parts.append("")

    # ================= INTELLIGENT LOG ANALYSIS (RAG) =================
    parts.append("RELEVANT LOG EVIDENCE (AI EXTRACTED):")

    logs = incident.logs.all()
    all_log_lines = []

    # 1. Saare logs collect karo
    for log in logs:
        if log.processed_content:
            # Content ko lines me todo aur clean karo
            lines = [l.strip() for l in log.processed_content.split('\n') if len(l.strip()) > 10]
            all_log_lines.extend(lines)

    if all_log_lines:
        # 2. FAISS Query Optimization
        # [FIX] Query mein Title + Description dono bhej rahe hain taaki search accurate ho
        search_query = f"{incident.title} {description_text}"

        # Hum 15 lines maang rahe hain jo sabse relevant hon
        relevant_lines = filter_relevant_logs(search_query, all_log_lines, top_k=15)

        if relevant_lines:
            for line in relevant_lines:
                parts.append(f"[LOG MATCH] {line}")
        else:
            parts.append("No direct error patterns found in logs via similarity search.")

            # Fallback: Agar FAISS fail ho jaye toh keyword search use karo
            infra = detect_infra_signals(logs, incident.description or "")
            if any(infra.values()):
                parts.append(f"Keyword Signals Detected: {infra}")
    else:
        parts.append("No log files available for analysis.")

    parts.append("")

    # ================= SIMILAR INCIDENTS =================
    try:
        similar = find_similar_incidents_db(incident.title, top_k=2)
        if similar:
            parts.append("SIMILAR PAST INCIDENTS:")
            for inc, score in similar:
                summary = f"- {inc.title} (Confidence: {score:.2f})"
                if inc.analysis and inc.analysis.root_cause:
                    summary += f" | Root Cause: {inc.analysis.root_cause}"
                parts.append(summary)
    except Exception:
        pass

    parts.append("")
    parts.append(
        "TASK: Based on the [LOG MATCH] lines above, identify the specific root cause. Explain logically why the system failed.")

    return "\n".join(parts)
