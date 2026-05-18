"""
Postmortem generation stays intentionally lightweight for production.
We already have the RCA payload, so this service should not import or boot models.
"""

def generate_postmortem(context: str, root_cause: str = None, explanation: str = None) -> str:
    if not root_cause:
        root_cause = "Investigation in progress"
    if not explanation:
        explanation = "Detailed analysis pending."

    # Estimated Impact (Heuristic)
    estimated_impact = "Service degradation"
    if "database" in root_cause.lower() or "redis" in root_cause.lower():
        estimated_impact = "Potential data unavailability or high latency"
    elif "50" in root_cause or "timeout" in root_cause.lower():
        estimated_impact = "User-facing errors (5xx) and dropped requests"
    elif "cpu" in root_cause.lower() or "memory" in root_cause.lower():
        estimated_impact = "Slow response times and potential pod evictions"

    # Infra-specific preventive actions based on keywords
    preventive_actions = []
    rc_lower = root_cause.lower()
    
    if "redis" in rc_lower:
        preventive_actions = [
            "- Implement circuit breakers (e.g., Hystrix/Resilience4j) for Redis connections",
            "- Audit Redis eviction policies (allkeys-lru vs volatile-lru) and memory limits",
            "- Verify Redis cluster high-availability failover mechanisms (Sentinel/Cluster)",
            "- Add monitoring for Redis command latency and fragmentation ratio"
        ]
    elif "db" in rc_lower or "database" in rc_lower or "postgres" in rc_lower:
        preventive_actions = [
            "- Tune database connection pool size (HikariCP/PgBouncer) based on active thread count",
            "- Review slow query logs (>100ms) and add missing compound indexes",
            "- Implement read replicas to offload read traffic",
            "- Check transaction isolation levels and lock contention"
        ]
    elif "dns" in rc_lower:
        preventive_actions = [
            "- Enable local DNS caching on nodes (nscd/dnsmasq) to reduce lookup latency",
            "- Audit DNS TTL settings to ensure fast propagation of failovers",
            "- Verify secondary DNS provider failover configuration",
            "- Check ndots configuration in Kubernetes /etc/resolv.conf"
        ]
    elif "kafka" in rc_lower or "queue" in rc_lower:
        preventive_actions = [
            "- Scale consumer groups to match partition count and handle lag",
            "- Increase message retention period to prevent data loss during outages",
            "- Implement dead-letter queues (DLQ) for poison messages",
            "- Tune producer batch size and linger.ms for throughput"
        ]
    elif "memory" in rc_lower or "oom" in rc_lower:
        preventive_actions = [
            "- Increase container memory limits (requests/limits) in Kubernetes/Docker",
            "- Audit application for memory leaks using profilers (e.g., pprof, JProfiler)",
            "- Configure heap dump on OOM for post-mortem analysis",
            "- Tune GC (Garbage Collection) settings for the specific workload"
        ]
    elif "cpu" in rc_lower:
        preventive_actions = [
            "- Optimize compute-intensive code paths (hot loops, serialization)",
            "- Implement horizontal pod autoscaling (HPA) based on CPU metrics",
            "- Review thread pool configurations to prevent starvation",
            "- Offload heavy processing to background workers"
        ]
    elif "disk" in rc_lower:
        preventive_actions = [
            "- Implement log rotation policies (logrotate) to prevent disk fill-up",
            "- Increase persistent volume size or switch to dynamic provisioning",
            "- Alert on disk usage > 80% to allow remediation time",
            "- Clean up temporary files /tmp regularly"
        ]
    else:
        preventive_actions = [
            "- Review service monitoring thresholds and alerts for false negatives",
            "- Audit dependent service SLAs and timeouts",
            "- Conduct chaos engineering tests for this failure mode",
            "- Improve logging granularity (debug/trace) in affected modules"
        ]

    # Construct the report
    report = (
        f"## Incident Postmortem\n\n"
        f"### Summary\n"
        f"**Root Cause:** {root_cause}\n"
        f"**Impact:** {estimated_impact}\n"
        f"**Details:** {explanation}\n\n"
        f"### Root Cause Analysis\n"
        f"The incident was triggered by {root_cause.lower()}. Technical evidence indicates {explanation.lower()} "
        f"This failure mode resulted in {estimated_impact.lower()}.\n\n"
        f"### Resolution\n"
        f"Service availability was restored by addressing the underlying {root_cause}. System metrics have returned to nominal levels.\n\n"
        f"### Actionable Next Steps\n"
        f"{chr(10).join(preventive_actions)}\n"
    )
    
    return report
