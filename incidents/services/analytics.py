from datetime import timedelta
from django.utils import timezone
from django.db import models
from django.db.models import Avg, Count, Q, DurationField, F, ExpressionWrapper
from django.core.cache import cache
from ..models import Incident


class AnalyticsService:
    """Service for calculating analytics and metrics"""
    
    CACHE_TIMEOUT = 300  # 5 minutes
    
    @staticmethod
    def get_dashboard_metrics(company):
        """Get all dashboard metrics for a company"""
        cache_key = f'dashboard_metrics_{company.id}'
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        incidents = Incident.objects.filter(company=company)
        now = timezone.now()
        
        # Time ranges
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)
        last_30d = now - timedelta(days=30)
        
        metrics = {
            'total_incidents_24h': incidents.filter(created_at__gte=last_24h).count(),
            'total_incidents_7d': incidents.filter(created_at__gte=last_7d).count(),
            'total_incidents_30d': incidents.filter(created_at__gte=last_30d).count(),
            'total_incidents_all': incidents.count(),
            'open_incidents': incidents.exclude(status__in=['resolved', 'closed']).count(),
            'resolved_incidents': incidents.filter(status='resolved').count(),
            'closed_incidents': incidents.filter(status='closed').count(),
        }
        
        # Calculate MTTR (Mean Time To Resolve)
        resolved = incidents.filter(resolved_at__isnull=False)
        if resolved.exists():
            mttr_data = resolved.annotate(
                resolution_time=ExpressionWrapper(
                    F('resolved_at') - F('created_at'),
                    output_field=DurationField()
                )
            ).aggregate(avg_time=Avg('resolution_time'))
            metrics['mttr_hours'] = mttr_data['avg_time'].total_seconds() / 3600 if mttr_data['avg_time'] else 0
        else:
            metrics['mttr_hours'] = 0
        
        # Calculate MTTD (Mean Time To Detect) - time from creation to first status change
        metrics['mttd_hours'] = 0  # Simplified - would need timeline data
        
        # Severity distribution
        metrics['severity_distribution'] = list(
            incidents.values('severity').annotate(count=Count('id')).order_by('-count')
        )
        
        # Status distribution
        metrics['status_distribution'] = list(
            incidents.values('status').annotate(count=Count('id')).order_by('-count')
        )

        # AI vs Manual Stats
        ai_analyzed = incidents.filter(
            analysis__ai_status='completed',
            analysis__confidence_score__gte=0.7
        ).count()
        manual_investigating = incidents.filter(status='investigating').count()
        other_incidents = incidents.exclude(
            id__in=incidents.filter(analysis__ai_status='completed', analysis__confidence_score__gte=0.7).values_list('id', flat=True)
        ).exclude(status='investigating').count()

        metrics['ai_vs_manual'] = {
            'ai_auto_analyzed': ai_analyzed,
            'manual_investigating': manual_investigating,
            'other': other_incidents
        }

        # Category distribution
        metrics['category_distribution'] = list(
            incidents.values('category').annotate(count=Count('id')).order_by('-count')
        )
        
        # Top affected services
        all_services = []
        for incident in incidents:
            all_services.extend(incident.affected_services or [])
        from collections import Counter
        service_counts = Counter(all_services)
        metrics['top_services'] = dict(service_counts.most_common(15))
        
        cache.set(cache_key, metrics, AnalyticsService.CACHE_TIMEOUT)
        return metrics
    
    @staticmethod
    def get_incidents_timeseries(company, days=30):
        """Get incidents count over time for charting"""
        cache_key = f'incidents_timeseries_{company.id}_{days}'
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        start_date = timezone.now() - timedelta(days=days)
        incidents = Incident.objects.filter(
            company=company,
            created_at__gte=start_date
        ).extra(
            select={'day': "date(created_at)"}
        ).values('day').annotate(count=Count('id')).order_by('day')
        
        # Also get MTTR trend for the same period
        resolved = Incident.objects.filter(
            company=company,
            resolved_at__isnull=False,
            created_at__gte=start_date
        ).annotate(
            resolution_time_hours=ExpressionWrapper(
                (F('resolved_at') - F('created_at')),
                output_field=DurationField()
            )
        ).extra(
            select={'day': "date(created_at)"}
        ).values('day').annotate(
            avg_mttr=Avg(F('resolution_time_hours'))
        ).order_by('day')

        # Convert MTTR to hours
        mttr_map = {r['day']: r['avg_mttr'].total_seconds() / 3600 if r['avg_mttr'] else 0 for r in resolved}
        
        data = []
        for inc in incidents:
            day = inc['day']
            data.append({
                'day': day,
                'count': inc['count'],
                'mttr': mttr_map.get(day, 0)
            })

        cache.set(cache_key, data, AnalyticsService.CACHE_TIMEOUT)
        return data
    
    @staticmethod
    def get_resolution_time_by_severity(company):
        """Get average resolution time grouped by severity"""
        resolved = Incident.objects.filter(
            company=company,
            resolved_at__isnull=False
        )
        
        data = resolved.annotate(
            resolution_time=ExpressionWrapper(
                F('resolved_at') - F('created_at'),
                output_field=DurationField()
            )
        ).values('severity').annotate(
            avg_hours=Avg(ExpressionWrapper(
                F('resolution_time').total_seconds() / 3600,
                output_field=models.FloatField()
            ))
        )
        
        return list(data)

