from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
import json
import tempfile

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Incident, IncidentLog, IncidentAnalysis, IncidentTimeline, Notification
)
from .forms import IncidentForm, IncidentCommentForm
from .services.analytics import AnalyticsService
from .services.notifications import NotificationService
from .services.log_ingestion_handler import handle_log_ingestion
from .tasks import (
    generate_incident_embedding,
    process_incident_logs,
    is_render_deployment
)
from accounts.models import CustomUser

from django.http import FileResponse
from incidents.services.postmortem_export import (
    export_postmortem_markdown,
    export_postmortem_pdf
)

# =========================================================
# DASHBOARD
# =========================================================
@login_required
def dashboard_view(request):
    company = request.company
    if not company:
        messages.error(request, "You must be associated with a company.")
        return redirect('profile')

    analytics = AnalyticsService()
    metrics = analytics.get_dashboard_metrics(company)
    timeseries = analytics.get_incidents_timeseries(company, days=30)

    start_of_day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    recent_incidents = Incident.objects.filter(
        company=company
    ).order_by('-created_at')[:10]
    active_critical_incidents = Incident.objects.filter(
        company=company,
        severity="critical",
    ).exclude(status__in=["resolved", "closed"]).count()
    occurrences_today = (
        Incident.objects.filter(company=company)
        .filter(
            Q(last_seen_at__gte=start_of_day)
            | Q(last_seen_at__isnull=True, created_at__gte=start_of_day)
        )
        .aggregate(total=Sum("occurrence_count"))["total"]
        or 0
    )
    top_spiking_issues = (
        Incident.objects.filter(company=company)
        .exclude(status__in=["resolved", "closed"])
        .order_by("-occurrence_count", "-last_seen_at")[:8]
    )

    context = {
        'metrics': metrics,
        'timeseries': json.dumps(list(timeseries), cls=DjangoJSONEncoder),
        'recent_incidents': recent_incidents,
        'metrics_json': json.dumps(metrics, cls=DjangoJSONEncoder),
        'active_critical_incidents': active_critical_incidents,
        'occurrences_today': occurrences_today,
        'top_spiking_issues': top_spiking_issues,
    }
    return render(request, 'incidents/dashboard.html', context)


# =========================================================
# INCIDENT LIST
# =========================================================
@login_required
def incident_list_view(request):
    company = request.company
    if not company:
        messages.error(request, "You must be associated with a company.")
        return redirect('profile')

    incidents = Incident.objects.filter(company=company)

    status_filter = request.GET.get('status')
    severity_filter = request.GET.get('severity')
    search = (request.GET.get('search') or '').strip()
    assigned_to = request.GET.get('assigned_to')

    if status_filter:
        incidents = incidents.filter(status=status_filter)
    if severity_filter:
        incidents = incidents.filter(severity=severity_filter)
    if assigned_to:
        incidents = incidents.filter(assigned_to_id=assigned_to)
    if search:
        incidents = incidents.filter(
            Q(title__icontains=search) |
            Q(description__icontains=search)
        )

    users = CustomUser.objects.filter(company=company).order_by('username')

    paginator = Paginator(incidents.order_by('-last_seen_at', '-created_at'), 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'incidents/list.html', {
        'page_obj': page_obj,
        'users': users,
        'search': search,
        'status': status_filter,
        'severity': severity_filter,
        'assigned_to': assigned_to,
    })


# =========================================================
# CREATE INCIDENT
# =========================================================
@login_required
def incident_create_view(request):
    company = request.company
    if not company:
        messages.error(request, "You must be associated with a company.")
        return redirect('profile')

    if request.method == 'POST':
        form = IncidentForm(request.POST, request.FILES, company=company)
        if form.is_valid():
            from incidents.services.log_processor import extract_log_content

            with transaction.atomic():
                incident = form.save(commit=False)
                incident.company = company
                incident.created_by = request.user
                incident.last_seen_at = timezone.now()
                incident.save()

                IncidentTimeline.objects.create(
                    incident=incident,
                    user=request.user,
                    action='created',
                    comment=f'Incident created: {incident.title}'
                )

                for file in request.FILES.getlist('log_files'):
                    raw_content, _ = extract_log_content(file)
                    if hasattr(file, "seek"):
                        file.seek(0)
                    IncidentLog.objects.create(
                        incident=incident,
                        file=file,
                        file_name=file.name,
                        file_size=file.size,
                        file_type=file.name.rsplit('.', 1)[-1].lower() if '.' in file.name else 'log',
                        processed=False,
                        raw_content=raw_content or "",
                        processed_content="",
                    )

                IncidentAnalysis.objects.get_or_create(
                    incident=incident,
                    defaults={"ai_status": "pending"}
                )

                _incident_id = str(incident.id)
                if is_render_deployment():
                    # Execute synchronously for Render free tier
                    transaction.on_commit(lambda iid=_incident_id: process_incident_logs(iid, True))
                    transaction.on_commit(lambda iid=_incident_id: generate_incident_embedding(iid))
                else:
                    transaction.on_commit(lambda iid=_incident_id: process_incident_logs.delay(iid, True))
                    transaction.on_commit(lambda iid=_incident_id: generate_incident_embedding.delay(iid))

            NotificationService.notify_incident_created(incident)
            messages.success(
                request,
                "Incident created. AI analysis is running in the background."
            )
            return redirect('incident_detail', incident_id=incident.id)
    else:
        form = IncidentForm(company=company)

    return render(request, 'incidents/create.html', {'form': form})


# =========================================================
# INCIDENT DETAIL
# =========================================================
@login_required
def incident_detail_view(request, incident_id):
    company = request.company
    if not company:
        return redirect('profile')

    incident = get_object_or_404(
        Incident.objects.prefetch_related('logs', 'comments'),
        id=incident_id,
        company=company,
    )

    # 🔥 ensure analysis always exists
    analysis, _ = IncidentAnalysis.objects.get_or_create(
        incident=incident,
        defaults={"ai_status": "pending"}
    )

    from .services.timeline import build_timeline
    technical_timeline = build_timeline(incident.logs.all())
    ai_report = {}
    if analysis:
        ai_report = analysis.full_ai_report or analysis.structured_output or {}
    recent_logs = incident.logs.order_by('-uploaded_at')[:20]

    return render(
        request,
        'incidents/detail.html',
        {
            'incident': incident,
            'analysis': analysis,
            'ai_report': ai_report,
            'recent_logs': recent_logs,
            'technical_timeline': technical_timeline,
            'comments': incident.comments.all(),
            'comment_form': IncidentCommentForm(),
        }
    )


# =========================================================
# ANALYSIS FEEDBACK
# =========================================================
@login_required
@require_POST
def mark_incident_resolved_feedback(request, incident_id):
    company = request.company
    if not company:
        messages.error(request, "You must be associated with a company.")
        return redirect('profile')

    incident = get_object_or_404(Incident, id=incident_id, company=company)
    old_status = incident.status
    incident.status = 'resolved'
    incident.needs_follow_up = False
    if not incident.resolved_at:
        incident.resolved_at = timezone.now()
    incident.save()

    IncidentTimeline.objects.create(
        incident=incident,
        user=request.user,
        action='resolved',
        from_state=old_status,
        to_state='resolved',
        comment='Marked resolved from AI report feedback.',
    )
    messages.success(request, "Incident marked as resolved.")
    return redirect('incident_detail', incident_id=incident.id)


@login_required
@require_POST
def mark_incident_fix_failed_feedback(request, incident_id):
    company = request.company
    if not company:
        messages.error(request, "You must be associated with a company.")
        return redirect('profile')

    incident = get_object_or_404(Incident, id=incident_id, company=company)
    old_status = incident.status
    incident.needs_follow_up = True
    if incident.status in ('resolved', 'closed'):
        incident.status = 'investigating'
    incident.save()

    analysis, _ = IncidentAnalysis.objects.get_or_create(
        incident=incident,
        defaults={"ai_status": 'pending'},
    )
    note = "[Fix didn't work — needs follow-up]"
    if analysis.feedback_notes:
        analysis.feedback_notes = f"{analysis.feedback_notes}\n{note}"
    else:
        analysis.feedback_notes = note
    analysis.save(update_fields=['feedback_notes', 'updated_at'])

    IncidentTimeline.objects.create(
        incident=incident,
        user=request.user,
        action='updated',
        from_state=old_status,
        to_state=incident.status,
        comment="Reported that the suggested fix did not work; incident kept open for follow-up.",
    )
    messages.warning(request, "Feedback recorded — incident left open for follow-up.")
    return redirect('incident_detail', incident_id=incident.id)


@login_required
@require_POST
def update_analysis_feedback(request, incident_id):
    company = request.company
    if not company:
        return JsonResponse({"error": "No company"}, status=403)

    incident = get_object_or_404(Incident, id=incident_id, company=company)
    analysis, _ = IncidentAnalysis.objects.get_or_create(
        incident=incident,
        defaults={"ai_status": "pending"},
    )

    analysis.user_root_cause = request.POST.get("user_root_cause", "").strip()
    analysis.feedback_notes = request.POST.get("feedback_notes", "").strip()
    analysis.is_corrected = True
    analysis.save(update_fields=["user_root_cause", "feedback_notes", "is_corrected", "updated_at"])

    messages.success(request, "Analysis feedback saved.")
    return redirect("incident_detail", incident_id=incident.id)


# =========================================================
# MANUAL AI TRIGGER
# =========================================================
@login_required
@require_http_methods(["POST"])
def trigger_ai_analysis(request, incident_id):
    company = request.company
    if not company:
        return JsonResponse({"error": "No company"}, status=403)
    
    incident = get_object_or_404(Incident, id=incident_id, company=company)

    IncidentAnalysis.objects.update_or_create(
        incident=incident,
        defaults={"ai_status": "pending", "error_message": ""}
    )

    if is_render_deployment():
        process_incident_logs(str(incident.id), True)
        generate_incident_embedding(str(incident.id))
    else:
        process_incident_logs.delay(str(incident.id), True)
        generate_incident_embedding.delay(str(incident.id))

    payload = {
        "status": "AI analysis started",
        "message": "Analysis will complete in a few moments",
    }
    accepts_json = "application/json" in request.headers.get("Accept", "")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if accepts_json or is_ajax:
        return JsonResponse(payload)

    messages.info(request, "AI re-analysis started in the background.")
    return redirect("incident_detail", incident_id=incident.id)


@login_required
def incident_analysis_status(request, incident_id):
    company = request.company
    if not company:
        return JsonResponse({"error": "No company"}, status=403)

    incident = get_object_or_404(Incident, id=incident_id, company=company)
    analysis, _ = IncidentAnalysis.objects.get_or_create(
        incident=incident,
        defaults={"ai_status": "pending"},
    )
    total_logs = incident.logs.count()
    processed_logs = incident.logs.filter(processed=True).count()

    return JsonResponse(
        {
            "incident_id": str(incident.id),
            "ai_status": analysis.ai_status,
            "error_message": analysis.error_message,
            "processed_logs": processed_logs,
            "total_logs": total_logs,
            "root_cause": analysis.root_cause,
            "explanation": analysis.explanation,
            "postmortem": analysis.postmortem,
            "updated_at": analysis.updated_at.isoformat(),
            "report": analysis.full_ai_report or analysis.structured_output or {},
        }
    )


# =========================================================
# LOG VIEWER
# =========================================================
@login_required
def log_viewer_view(request, log_id):
    log = get_object_or_404(IncidentLog, id=log_id)
    return render(
        request,
        'incidents/log_viewer.html',
        {'log': log, 'content': log.processed_content or ""}
    )


# =========================================================
# POSTMORTEM DOWNLOADS
# =========================================================
@login_required
def download_postmortem_md(request, incident_id):
    incident = get_object_or_404(Incident, id=incident_id)
    filename, content = export_postmortem_markdown(incident)

    response = HttpResponse(content, content_type="text/markdown")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def download_postmortem_pdf(request, incident_id):
    incident = get_object_or_404(Incident, id=incident_id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        export_postmortem_pdf(incident, tmp.name)
        return FileResponse(
            open(tmp.name, "rb"),
            as_attachment=True,
            filename=f"postmortem_{incident.id}.pdf"
        )


# =========================================================
# PAST INCIDENTS
# =========================================================
@login_required
def past_incidents_view(request):
    company = request.company
    if not company:
        messages.error(request, "You must be associated with a company.")
        return redirect('profile')

    incidents = Incident.objects.filter(
        company=company,
        status__in=["resolved", "closed"]
    ).order_by("-created_at")

    paginator = Paginator(incidents, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "incidents/past_incidents.html", {"page_obj": page_obj})


# =========================================================
# LOG EXPLORER
# =========================================================
@login_required
def log_explorer_view(request):
    recent_logs = IncidentLog.objects.select_related(
        'incident'
    ).order_by('-uploaded_at')[:20]

    return render(
        request,
        'incidents/log_explorer.html',
        {'recent_logs': recent_logs}
    )


# =========================================================
# NOTIFICATIONS
# =========================================================
@login_required
def notifications_view(request):
    if request.method == 'POST':
        if 'mark_all_read' in request.POST:
            request.user.notifications.filter(read=False).update(read=True)
            messages.success(request, 'All notifications marked as read.')
        elif 'mark_read' in request.POST:
            notification_id = request.POST.get('notification_id')
            if notification_id:
                notification = get_object_or_404(Notification, id=notification_id, user=request.user)
                notification.read = True
                notification.save()
                messages.success(request, 'Notification marked as read.')
        return redirect('notifications')

    return render(
        request,
        'incidents/notifications.html',
        {'notifications': request.user.notifications.all()[:50]}
    )

@login_required
@require_http_methods(["POST"])
def ai_explain_logs(request):
    from incidents.services.text_generation import generate_root_cause

    log_content = request.POST.get("log_content", "")
    if not log_content:
        return HttpResponse("No log content provided")

    result = generate_root_cause(log_content)
    explanation = result.get("raw", "")

    return HttpResponse(
        f"<pre class='whitespace-pre-wrap'>{explanation}</pre>"
    )


# =========================================================
# INCIDENT UPDATE VIEW (FIX FOR CELERY ERROR)
# =========================================================
@login_required
@require_POST
def incident_update_view(request, incident_id):
    company = request.company
    if not company:
        return JsonResponse({"error": "No company"}, status=403)

    incident = get_object_or_404(
        Incident,
        id=incident_id,
        company=company
    )

    old_status = incident.status
    new_status = request.POST.get("status")

    if new_status and new_status in dict(Incident.STATUS_CHOICES):
        incident.status = new_status

        if new_status == "resolved" and not incident.resolved_at:
            incident.resolved_at = timezone.now()

        IncidentTimeline.objects.create(
            incident=incident,
            user=request.user,
            action="status_changed",
            from_state=old_status,
            to_state=new_status,
        )

        NotificationService.notify_status_changed(
            incident, request.user, old_status, new_status
        )

    incident.save()
    messages.success(request, "Incident updated successfully")
    return redirect("incident_detail", incident_id=incident.id)

@login_required
def export_incidents_csv(request):
    import csv
    from django.http import HttpResponse
    
    company = request.company
    incidents = Incident.objects.filter(company=company).order_by('-created_at')
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="incidents_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['ID', 'Title', 'Severity', 'Status', 'Category', 'Created At', 'Created By', 'Assigned To'])
    
    for incident in incidents:
        writer.writerow([
            incident.incident_id,
            incident.title,
            incident.get_severity_display(),
            incident.get_status_display(),
            incident.category,
            incident.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            incident.created_by.username if incident.created_by else 'System',
            incident.assigned_to.username if incident.assigned_to else 'Unassigned'
        ])
    
    return response

# =========================================================
# ANALYTICS API VIEW (FIX FOR CELERY)
# =========================================================
@login_required
def analytics_api_view(request):
    company = request.company
    if not company:
        return JsonResponse({"error": "No company"}, status=403)

    analytics = AnalyticsService()

    return JsonResponse({
        "metrics": analytics.get_dashboard_metrics(company),
        "timeseries": list(
            analytics.get_incidents_timeseries(company, days=30)
        ),
    })


class LogIngestionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        company = getattr(request.user, "company", None) or getattr(request, "company", None)
        if not company:
            return Response(
                {"error": "Authenticated user is not linked to a company."},
                status=status.HTTP_403_FORBIDDEN,
            )

        incident_id = request.data.get("incident_id")
        log_text = request.data.get("log_text")
        uploaded_file = request.FILES.get("file")

        if not log_text and not uploaded_file:
            return Response(
                {"error": "Provide log_text or file."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if uploaded_file and uploaded_file.size == 0:
            return Response(
                {"error": "Uploaded file is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            incident, log_entry, meta = handle_log_ingestion(
                company=company,
                user=request.user,
                incident_id=incident_id,
                log_text=log_text,
                uploaded_file=uploaded_file,
            )
        except LookupError:
            return Response(
                {"error": "Incident not found for this company."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "status": "received",
                "incident_id": str(incident.id),
                "log_id": log_entry.id,
                "deduplicated": meta["deduplicated"],
                "new_incident": meta["new_incident"],
                "analysis_status_url": reverse(
                    "incident_analysis_status",
                    kwargs={"incident_id": incident.id},
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )
