from django.urls import path
from . import views

urlpatterns = [
    path('api/log-ingestion/', views.LogIngestionAPIView.as_view(), name='log_ingestion_api'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('incidents/', views.incident_list_view, name='incident_list'),
    path('incidents/create/', views.incident_create_view, name='incident_create'),
    path('incidents/<uuid:incident_id>/', views.incident_detail_view, name='incident_detail'),
    path('incidents/<uuid:incident_id>/analysis-status/', views.incident_analysis_status, name='incident_analysis_status'),
    path('incidents/<uuid:incident_id>/analysis-feedback/', views.update_analysis_feedback, name='update_analysis_feedback'),
    path('incidents/<uuid:incident_id>/feedback/resolved/', views.mark_incident_resolved_feedback, name='mark_incident_resolved_feedback'),
    path('incidents/<uuid:incident_id>/feedback/fix-failed/', views.mark_incident_fix_failed_feedback, name='mark_incident_fix_failed_feedback'),
    path('incidents/<uuid:incident_id>/update/', views.incident_update_view, name='incident_update'),
    path('incidents/<uuid:incident_id>/trigger_ai_analysis/', views.trigger_ai_analysis, name='trigger_ai_analysis'),
    path('incidents/past/', views.past_incidents_view, name='past_incidents'),
    path('incidents/export/csv/', views.export_incidents_csv, name='export_incidents_csv'),
    path('logs/explorer/', views.log_explorer_view, name='log_explorer'),
    path('logs/<int:log_id>/', views.log_viewer_view, name='log_viewer'),
    path('notifications/', views.notifications_view, name='notifications'),
    path('api/analytics/', views.analytics_api_view, name='analytics_api'),
    path("api/ai/explain-logs/", views.ai_explain_logs, name="ai_explain_logs"),

    # AI Endpoints


path(
    "incident/<uuid:incident_id>/postmortem/md/",
    views.download_postmortem_md,
    name="download_postmortem_md",
),
path(
    "incident/<uuid:incident_id>/postmortem/pdf/",
    views.download_postmortem_pdf,
    name="download_postmortem_pdf",
),
]
