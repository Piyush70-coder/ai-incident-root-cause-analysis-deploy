from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from incidents.models import Incident, IncidentAnalysis, IncidentComment, IncidentLog
from incidents.services.log_ingestion_handler import handle_log_ingestion
from .serializers import IncidentSerializer, IncidentCommentSerializer, IncidentLogSerializer


class IncidentViewSet(viewsets.ModelViewSet):
    """API ViewSet for incidents"""
    serializer_class = IncidentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        company = self.request.user.company
        if not company:
            return Incident.objects.none()
        return Incident.objects.filter(company=company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company, created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        incident = self.get_object()
        serializer = IncidentCommentSerializer(data=request.data)

        if serializer.is_valid():
            serializer.save(incident=incident, user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        incident = self.get_object()
        logs = incident.logs.all()
        serializer = IncidentLogSerializer(logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def analysis_status(self, request, pk=None):
        incident = self.get_object()
        analysis, _ = IncidentAnalysis.objects.get_or_create(
            incident=incident,
            defaults={"ai_status": "pending"},
        )
        return Response(
            {
                "incident_id": str(incident.id),
                "ai_status": analysis.ai_status,
                "error_message": analysis.error_message,
                "processed_logs": incident.logs.filter(processed=True).count(),
                "total_logs": incident.logs.count(),
                "root_cause": analysis.root_cause,
                "explanation": analysis.explanation,
                "postmortem": analysis.postmortem,
                "report": analysis.full_ai_report or analysis.structured_output or {},
                "updated_at": analysis.updated_at,
            }
        )


class LogIngestionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        company = getattr(request.user, "company", None)
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
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "status": "received",
                "incident_id": str(incident.id),
                "log_id": log_entry.id,
                "deduplicated": meta["deduplicated"],
                "new_incident": meta["new_incident"],
            },
            status=status.HTTP_202_ACCEPTED,
        )
