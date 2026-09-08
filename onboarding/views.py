from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .activity import log_event, touch_agent
from .constants import ROLE_RECRUITER
from .models import (
    AgentCarrierRequirement,
    AgentChecklistItem,
    Carrier,
    ChecklistItemDefinition,
    Cohort,
    OnboardingAgent,
    OnboardingEvent,
    OnboardingSettings,
    RequirementTemplate,
    SpreadsheetSyncState,
)
from .permissions import (
    HasOnboardingAccess,
    IsOnboardingAssistant,
    OnboardingWritePermission,
    onboarding_role,
    recruiter_can_edit_item,
)
from .querysets import agent_queryset, template_queryset
from .serializers import (
    AgentCarrierSerializer,
    AgentChecklistItemSerializer,
    BulkAgentSerializer,
    CarrierSerializer,
    ChecklistItemDefinitionSerializer,
    CohortSerializer,
    OnboardingAgentListSerializer,
    OnboardingAgentSerializer,
    OnboardingEventSerializer,
    OnboardingSettingsSerializer,
    RequirementTemplateSerializer,
    StaffUserSerializer,
)
from .services.assign import assign_requirements
from .services.person import PersonLinkError, apply_person_link
from ghl.services import GhlError, link_mirrored_user
from .services.progress import annotate_agent
from .services.status import STATUS_AT_RISK, STATUS_ON_TRACK, STATUS_OVERDUE

User = get_user_model()


class CohortViewSet(viewsets.ModelViewSet):
    serializer_class = CohortSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return Cohort.objects.annotate(agent_count=Count('agents'))

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [HasOnboardingAccess()]
        return [IsOnboardingAssistant()]


class CarrierViewSet(viewsets.ModelViewSet):
    serializer_class = CarrierSerializer
    queryset = Carrier.objects.all()
    pagination_class = None

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [HasOnboardingAccess()]
        return [IsOnboardingAssistant()]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            instance.delete()
        except ProtectedError:
            return Response(
                {
                    'detail': (
                        f'Cannot delete "{instance.name}" because agents still '
                        'have this carrier assigned.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChecklistDefinitionViewSet(viewsets.ModelViewSet):
    serializer_class = ChecklistItemDefinitionSerializer
    queryset = ChecklistItemDefinition.objects.all()
    pagination_class = None

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [HasOnboardingAccess()]
        return [IsOnboardingAssistant()]


class RequirementTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = RequirementTemplateSerializer
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return template_queryset()

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [HasOnboardingAccess()]
        return [IsOnboardingAssistant()]

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class AgentViewSet(viewsets.ModelViewSet):
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = agent_queryset()
        cohort = self.request.query_params.get('cohort')
        owner = self.request.query_params.get('owner')
        search = self.request.query_params.get('search', '').strip()
        if cohort:
            qs = qs.filter(cohort_id=cohort)
        if owner:
            qs = qs.filter(owner_id=owner)
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search)
                | Q(email__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__username__icontains=search)
            )
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return OnboardingAgentListSerializer
        return OnboardingAgentSerializer

    def get_permissions(self):
        if self.action in ('create', 'destroy', 'bulk'):
            return [IsOnboardingAssistant()]
        if self.action in ('partial_update',):
            return [OnboardingWritePermission()]
        return [HasOnboardingAccess()]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['settings'] = OnboardingSettings.load()
        return ctx

    def _with_progress(self, agent, serializer_class=None):
        settings = OnboardingSettings.load()
        extra = annotate_agent(agent, settings)
        cls = serializer_class or self.get_serializer_class()
        return cls(
            agent, context={**self.get_serializer_context(), 'progress': extra}
        ).data

    def _filter_status(self, agents, wanted):
        if not wanted:
            return agents
        settings = OnboardingSettings.load()
        kept = []
        for agent in agents:
            extra = annotate_agent(agent, settings)
            if extra['status'] == wanted:
                kept.append(agent)
        return kept

    def list(self, request, *args, **kwargs):
        wanted = request.query_params.get('status')
        queryset = self.get_queryset()
        if wanted:
            agents = self._filter_status(list(queryset), wanted)
            page = self.paginate_queryset(agents)
            if page is not None:
                data = [
                    self._with_progress(a, OnboardingAgentListSerializer) for a in page
                ]
                return self.get_paginated_response(data)
            data = [
                self._with_progress(a, OnboardingAgentListSerializer) for a in agents
            ]
            return Response(data)
        page = self.paginate_queryset(queryset)
        agents = page if page is not None else queryset
        data = [self._with_progress(a, OnboardingAgentListSerializer) for a in agents]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def retrieve(self, request, *args, **kwargs):
        return Response(self._with_progress(self.get_object()))

    def perform_create(self, serializer):
        with transaction.atomic():
            agent = serializer.save(last_updated_by=self.request.user)
            if not agent.start_date:
                agent.start_date = agent.cohort.start_date
                agent.save(update_fields=['start_date'])
            assign_requirements(agent)
            log_event(
                self.request.user,
                agent,
                OnboardingEvent.Action.AGENT_CREATED,
                agent.full_name,
            )
            touch_agent(agent, self.request.user)
        self._created = agent

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        agent = agent_queryset().get(pk=self._created.pk)
        return Response(self._with_progress(agent), status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        role = onboarding_role(self.request.user)
        if role == ROLE_RECRUITER:
            allowed = {'manually_at_risk'}
            extra = set(serializer.validated_data) - allowed
            if extra:
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied(
                    'Recruiters can only flag at-risk on agents they own.'
                )
        was_risk = serializer.instance.manually_at_risk
        agent = serializer.save()
        if agent.manually_at_risk and not was_risk:
            log_event(
                self.request.user,
                agent,
                OnboardingEvent.Action.AT_RISK,
                'Marked at risk',
            )
        touch_agent(agent, self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        self.check_object_permissions(request, instance)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        agent = agent_queryset().get(pk=instance.pk)
        return Response(self._with_progress(agent))

    def perform_destroy(self, instance):
        instance.delete()

    @action(detail=False, methods=['post'], url_path='bulk')
    def bulk(self, request):
        serializer = BulkAgentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            cohort = data.get('cohort')
            if cohort is None:
                cohort = Cohort.objects.create(
                    name=data['cohort_name'],
                    start_date=data['start_date'],
                )
            default_owner = data.get('owner')
            created = []
            for row in data['agents']:
                ghl_user = row.pop('_ghl_user', None)
                row.pop('ghl_user_id', None)
                agent = OnboardingAgent.objects.create(
                    full_name=row['full_name'].strip(),
                    email=(row.get('email') or '').strip(),
                    cohort=cohort,
                    start_date=row.get('start_date') or cohort.start_date,
                    owner=row.get('owner') or default_owner,
                    role=row.get('role', ''),
                    state=row.get('state', ''),
                    agent_type=row.get('agent_type', ''),
                    last_updated_by=request.user,
                )
                try:
                    apply_person_link(agent, email=agent.email)
                except PersonLinkError as exc:
                    raise ValidationError(
                        {'agents': {exc.field: str(exc)}}
                    ) from exc
                if ghl_user is not None and agent.user_id:
                    try:
                        link_mirrored_user(ghl_user.ghl_id, agent.user)
                    except GhlError as exc:
                        raise ValidationError(
                            {'agents': {'ghl_user_id': str(exc)}}
                        ) from exc
                assign_requirements(agent)
                log_event(
                    request.user,
                    agent,
                    OnboardingEvent.Action.AGENT_CREATED,
                    agent.full_name,
                )
                touch_agent(agent, request.user)
                created.append(agent.id)
        agents = list(agent_queryset().filter(id__in=created))
        body = [self._with_progress(a) for a in agents]
        return Response(
            {'cohort': CohortSerializer(cohort).data, 'agents': body},
            status=status.HTTP_201_CREATED,
        )


class MyOnboardingView(APIView):
    """The logged-in person's own onboarding case.

    Staff use /agents/:id/. This is what the GHL iframe loads after autologin.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        agent = agent_queryset().filter(user=request.user).first()
        if agent is None and (request.user.email or '').strip():
            agent = (
                agent_queryset()
                .filter(email__iexact=request.user.email)
                .first()
            )
            if agent is not None and agent.user_id is None:
                agent.user = request.user
                agent.save(update_fields=['user'])
            elif agent is not None and agent.user_id != request.user.id:
                agent = None
        if agent is None:
            return Response(
                {'detail': 'No onboarding case is linked to this account.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        settings = OnboardingSettings.load()
        extra = annotate_agent(agent, settings)
        data = OnboardingAgentSerializer(
            agent,
            context={'request': request, 'progress': extra, 'settings': settings},
        ).data
        staff = User.objects.filter(is_staff=True, is_active=True).order_by(
            'first_name', 'username'
        )
        data['staff'] = StaffUserSerializer(staff, many=True).data
        return Response(data)


class ChecklistItemView(APIView):
    permission_classes = [OnboardingWritePermission]

    def patch(self, request, agent_id, item_id):
        try:
            item = AgentChecklistItem.objects.select_related('agent', 'owner').get(
                pk=item_id, agent_id=agent_id
            )
        except AgentChecklistItem.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)
        self.check_object_permissions(request, item)

        role = onboarding_role(request.user)
        if role == ROLE_RECRUITER and not recruiter_can_edit_item(
            request.user, item.agent, item
        ):
            return Response({'detail': 'You can only update items assigned to you.'}, status=403)

        completed = request.data.get('is_completed')
        if completed is not None:
            item.is_completed = bool(completed)
            if item.is_completed:
                item.completed_by = request.user
                item.completed_at = timezone.now()
                log_event(
                    request.user,
                    item.agent,
                    OnboardingEvent.Action.CHECKLIST_COMPLETED,
                    item.label,
                )
            else:
                item.completed_by = None
                item.completed_at = None
                log_event(
                    request.user,
                    item.agent,
                    OnboardingEvent.Action.CHECKLIST_UNCOMPLETED,
                    item.label,
                )

        if 'is_flagged' in request.data:
            if onboarding_role(request.user) is None:
                return Response(
                    {'detail': 'Only staff can flag items.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            item.is_flagged = bool(request.data['is_flagged'])
            log_event(
                request.user,
                item.agent,
                OnboardingEvent.Action.FLAGGED
                if item.is_flagged
                else OnboardingEvent.Action.UNFLAGGED,
                item.label,
            )
        if 'comment' in request.data:
            item.comment = request.data['comment'] or ''
            log_event(
                request.user,
                item.agent,
                OnboardingEvent.Action.COMMENT,
                item.label,
            )
        if 'owner_id' in request.data:
            owner_id = request.data['owner_id']
            item.owner = (
                User.objects.filter(pk=owner_id, is_staff=True).first()
                if owner_id
                else None
            )
            log_event(
                request.user,
                item.agent,
                OnboardingEvent.Action.OWNER_ASSIGNED,
                item.label,
            )

        item.save()
        touch_agent(item.agent, request.user)
        return Response(AgentChecklistItemSerializer(item).data)


class CarrierRequirementView(APIView):
    permission_classes = [OnboardingWritePermission]

    def patch(self, request, agent_id, req_id):
        try:
            req = AgentCarrierRequirement.objects.select_related(
                'agent', 'carrier', 'owner'
            ).get(pk=req_id, agent_id=agent_id)
        except AgentCarrierRequirement.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)
        self.check_object_permissions(request, req)

        role = onboarding_role(request.user)
        if role == ROLE_RECRUITER and not recruiter_can_edit_item(
            request.user, req.agent, req
        ):
            return Response(
                {'detail': 'You can only update items assigned to you.'}, status=403
            )

        if 'status' in request.data:
            new_status = request.data['status']
            valid = {c.value for c in AgentCarrierRequirement.Status}
            if new_status not in valid:
                return Response({'status': 'Invalid carrier status.'}, status=400)
            req.status = new_status
            if new_status == AgentCarrierRequirement.Status.APPROVED:
                req.completed_by = request.user
                req.completed_at = timezone.now()
            else:
                req.completed_by = None
                req.completed_at = None
            log_event(
                request.user,
                req.agent,
                OnboardingEvent.Action.CARRIER_STATUS,
                f'{req.carrier.name}: {req.get_status_display()}',
            )
        if 'is_flagged' in request.data:
            if onboarding_role(request.user) is None:
                return Response(
                    {'detail': 'Only staff can flag items.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            req.is_flagged = bool(request.data['is_flagged'])
            log_event(
                request.user,
                req.agent,
                OnboardingEvent.Action.FLAGGED
                if req.is_flagged
                else OnboardingEvent.Action.UNFLAGGED,
                req.carrier.name,
            )
        if 'comment' in request.data:
            req.comment = request.data['comment'] or ''
            log_event(
                request.user,
                req.agent,
                OnboardingEvent.Action.COMMENT,
                req.carrier.name,
            )
        if 'owner_id' in request.data:
            owner_id = request.data['owner_id']
            req.owner = (
                User.objects.filter(pk=owner_id, is_staff=True).first()
                if owner_id
                else None
            )
            log_event(
                request.user,
                req.agent,
                OnboardingEvent.Action.OWNER_ASSIGNED,
                req.carrier.name,
            )

        req.save()
        touch_agent(req.agent, request.user)
        return Response(AgentCarrierSerializer(req).data)


class DashboardView(APIView):
    permission_classes = [HasOnboardingAccess]

    def get(self, request):
        settings = OnboardingSettings.load()
        agents = list(agent_queryset())
        buckets = {
            STATUS_ON_TRACK: 0,
            STATUS_AT_RISK: 0,
            STATUS_OVERDUE: 0,
        }
        percents = []
        outstanding = 0
        for agent in agents:
            extra = annotate_agent(agent, settings)
            buckets[extra['status']] += 1
            percents.append(extra['completion_percent'])
            outstanding += len(extra['outstanding'])
        overall = round(sum(percents) / len(percents)) if percents else 0
        events = (
            OnboardingEvent.objects.select_related('actor', 'agent')
            .order_by('-created_at')[:12]
        )
        cohorts = Cohort.objects.filter(is_active=True).annotate(
            agent_count=Count('agents')
        )
        return Response(
            {
                'active_cohorts': cohorts.count(),
                'agent_count': len(agents),
                'overall_completion': overall,
                'on_track': buckets[STATUS_ON_TRACK],
                'at_risk': buckets[STATUS_AT_RISK],
                'overdue': buckets[STATUS_OVERDUE],
                'outstanding_count': outstanding,
                'cohorts': CohortSerializer(cohorts, many=True).data,
                'recent_activity': OnboardingEventSerializer(events, many=True).data,
            }
        )


class AuditView(APIView):
    permission_classes = [HasOnboardingAccess]

    def get(self, request):
        settings = OnboardingSettings.load()
        qs = agent_queryset()
        cohort = request.query_params.get('cohort')
        owner = request.query_params.get('owner')
        flagged_only = request.query_params.get('flagged') in ('1', 'true', 'yes')
        if cohort:
            qs = qs.filter(cohort_id=cohort)
        else:
            qs = qs.filter(cohort__is_active=True)
        if owner:
            qs = qs.filter(owner_id=owner)

        rows = []
        for agent in qs:
            extra = annotate_agent(agent, settings)
            extra_status = extra['status']
            for item in extra['items']:
                if item.is_required and item.is_completed:
                    continue
                if not item.is_required and not item.is_flagged:
                    continue
                if item.is_completed and not item.is_flagged:
                    continue
                if flagged_only and not item.is_flagged:
                    continue
                if item.is_required and not item.is_completed:
                    rows.append(_audit_row(agent, extra_status, item, 'checklist'))
            for req in extra['carriers']:
                if req.is_required and req.is_complete and not req.is_flagged:
                    continue
                if not req.is_required and not req.is_flagged:
                    continue
                if flagged_only and not req.is_flagged:
                    continue
                if req.is_required and not req.is_complete:
                    rows.append(_audit_row(agent, extra_status, req, 'carrier'))
                elif req.is_flagged:
                    rows.append(_audit_row(agent, extra_status, req, 'carrier'))
        return Response({'results': rows, 'count': len(rows)})


def _audit_row(agent, agent_status, obj, kind):
    if kind == 'checklist':
        label = obj.label
        carrier = None
        item_status = 'completed' if obj.is_completed else 'incomplete'
        item_id = obj.id
    else:
        label = obj.carrier.name
        carrier = obj.carrier.name
        item_status = obj.status
        item_id = obj.id
    from .serializers import user_brief

    return {
        'agent_id': agent.id,
        'agent_name': agent.full_name,
        'cohort_id': agent.cohort_id,
        'cohort_name': agent.cohort.name,
        'start_date': agent.start_date,
        'item_type': kind,
        'item_id': item_id,
        'label': label,
        'carrier': carrier,
        'owner': user_brief(obj.owner) or user_brief(agent.owner),
        'status': item_status,
        'agent_status': agent_status,
        'comment': obj.comment,
        'is_flagged': obj.is_flagged,
        'due_context': f'Started {agent.start_date.isoformat()}',
    }


class SettingsView(APIView):
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [HasOnboardingAccess()]
        return [IsOnboardingAssistant()]

    def get(self, request):
        return Response(OnboardingSettingsSerializer(OnboardingSettings.load()).data)

    def patch(self, request):
        settings = OnboardingSettings.load()
        serializer = OnboardingSettingsSerializer(
            settings, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(OnboardingSettingsSerializer(OnboardingSettings.load()).data)


class SyncView(APIView):
    permission_classes = [IsOnboardingAssistant]

    def get(self, request):
        from .services.sheets import credentials_configured, sync_status_payload

        return Response(sync_status_payload())

    def post(self, request):
        from .services.sheets import credentials_configured, push_agents

        if not credentials_configured():
            state = SpreadsheetSyncState.load()
            state.configured = False
            state.last_error = (
                'Google Sheets is not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE '
                'or GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEETS_SPREADSHEET_ID.'
            )
            state.last_error_at = timezone.now()
            state.save()
            return Response(
                {
                    'ok': False,
                    'configured': False,
                    'error': state.last_error,
                },
                status=400,
            )
        result = push_agents(force=True)
        return Response(result)


class StaffListView(APIView):
    permission_classes = [HasOnboardingAccess]

    def get(self, request):
        users = User.objects.filter(is_staff=True, is_active=True).order_by(
            'first_name', 'username'
        )
        return Response(StaffUserSerializer(users, many=True).data)
