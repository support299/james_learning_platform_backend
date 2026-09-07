from django.contrib.auth import get_user_model
from django.utils.text import slugify
from rest_framework import serializers

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
    TemplateCarrier,
    TemplateChecklistItem,
)
from .services.person import PersonLinkError, apply_person_link
from .services.progress import annotate_agent
from ghl.models import GhlUser
from ghl.services import GhlError, link_mirrored_user

User = get_user_model()


def user_brief(user):
    if user is None:
        return None
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
    }


class CarrierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Carrier
        fields = ['id', 'name', 'code', 'line', 'is_active', 'sort_order']
        extra_kwargs = {'code': {'required': False, 'allow_blank': True}}

    def validate_code(self, value):
        return slugify(value) if value else value

    def create(self, validated):
        if not validated.get('code') and validated.get('name'):
            validated['code'] = slugify(validated['name'])
        return super().create(validated)


class ChecklistItemDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistItemDefinition
        fields = ['id', 'label', 'is_required', 'is_active', 'sort_order']


class TemplateCarrierWriteSerializer(serializers.Serializer):
    carrier = serializers.PrimaryKeyRelatedField(queryset=Carrier.objects.all())
    is_required = serializers.BooleanField(default=True)


class TemplateItemWriteSerializer(serializers.Serializer):
    definition = serializers.PrimaryKeyRelatedField(
        queryset=ChecklistItemDefinition.objects.all()
    )
    is_required = serializers.BooleanField(default=True)
    sort_order = serializers.IntegerField(default=0)


class RequirementTemplateSerializer(serializers.ModelSerializer):
    carriers = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    carrier_ids = TemplateCarrierWriteSerializer(
        many=True, write_only=True, required=False
    )
    item_ids = TemplateItemWriteSerializer(many=True, write_only=True, required=False)

    class Meta:
        model = RequirementTemplate
        fields = [
            'id',
            'name',
            'role',
            'state',
            'agent_type',
            'is_default',
            'is_active',
            'carriers',
            'items',
            'carrier_ids',
            'item_ids',
            'updated_at',
        ]

    def get_carriers(self, obj):
        return [
            {
                'id': link.id,
                'carrier': link.carrier_id,
                'name': link.carrier.name,
                'line': link.carrier.line,
                'is_required': link.is_required,
            }
            for link in obj.template_carriers.all()
        ]

    def get_items(self, obj):
        return [
            {
                'id': link.id,
                'definition': link.definition_id,
                'label': link.definition.label,
                'is_required': link.is_required,
                'sort_order': link.sort_order,
            }
            for link in obj.template_items.all()
        ]

    def _sync_links(self, template, carrier_ids, item_ids):
        if carrier_ids is not None:
            template.template_carriers.all().delete()
            TemplateCarrier.objects.bulk_create(
                [
                    TemplateCarrier(
                        template=template,
                        carrier=row['carrier'],
                        is_required=row['is_required'],
                    )
                    for row in carrier_ids
                ]
            )
        if item_ids is not None:
            template.template_items.all().delete()
            TemplateChecklistItem.objects.bulk_create(
                [
                    TemplateChecklistItem(
                        template=template,
                        definition=row['definition'],
                        is_required=row['is_required'],
                        sort_order=row['sort_order'],
                    )
                    for row in item_ids
                ]
            )

    def _clear_other_defaults(self, template):
        if template.is_default:
            RequirementTemplate.objects.exclude(pk=template.pk).filter(
                is_default=True
            ).update(is_default=False)

    def create(self, validated):
        carrier_ids = validated.pop('carrier_ids', None)
        item_ids = validated.pop('item_ids', None)
        template = RequirementTemplate.objects.create(**validated)
        self._clear_other_defaults(template)
        self._sync_links(template, carrier_ids, item_ids)
        return template

    def update(self, instance, validated):
        carrier_ids = validated.pop('carrier_ids', None)
        item_ids = validated.pop('item_ids', None)
        for key, value in validated.items():
            setattr(instance, key, value)
        instance.save()
        self._clear_other_defaults(instance)
        self._sync_links(instance, carrier_ids, item_ids)
        return instance


class CohortSerializer(serializers.ModelSerializer):
    agent_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Cohort
        fields = [
            'id',
            'name',
            'start_date',
            'is_active',
            'notes',
            'agent_count',
            'created_at',
            'updated_at',
        ]


class AgentChecklistItemSerializer(serializers.ModelSerializer):
    owner = serializers.SerializerMethodField()
    completed_by = serializers.SerializerMethodField()
    owner_id = serializers.PrimaryKeyRelatedField(
        source='owner',
        queryset=User.objects.filter(is_staff=True),
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = AgentChecklistItem
        fields = [
            'id',
            'label',
            'is_required',
            'is_completed',
            'completed_by',
            'completed_at',
            'owner',
            'owner_id',
            'comment',
            'is_flagged',
            'sort_order',
            'updated_at',
        ]
        read_only_fields = ['label', 'is_required', 'completed_by', 'completed_at']

    def get_owner(self, obj):
        return user_brief(obj.owner)

    def get_completed_by(self, obj):
        return user_brief(obj.completed_by)


class AgentCarrierSerializer(serializers.ModelSerializer):
    carrier_name = serializers.CharField(source='carrier.name', read_only=True)
    carrier_code = serializers.CharField(source='carrier.code', read_only=True)
    carrier_line = serializers.CharField(source='carrier.line', read_only=True)
    owner = serializers.SerializerMethodField()
    completed_by = serializers.SerializerMethodField()
    owner_id = serializers.PrimaryKeyRelatedField(
        source='owner',
        queryset=User.objects.filter(is_staff=True),
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = AgentCarrierRequirement
        fields = [
            'id',
            'carrier',
            'carrier_name',
            'carrier_code',
            'carrier_line',
            'status',
            'is_required',
            'owner',
            'owner_id',
            'comment',
            'is_flagged',
            'completed_by',
            'completed_at',
            'updated_at',
        ]
        read_only_fields = ['carrier', 'is_required', 'completed_by', 'completed_at']

    def get_owner(self, obj):
        return user_brief(obj.owner)

    def get_completed_by(self, obj):
        return user_brief(obj.completed_by)


class OnboardingAgentSerializer(serializers.ModelSerializer):
    owner = serializers.SerializerMethodField()
    last_updated_by = serializers.SerializerMethodField()
    user = serializers.SerializerMethodField()
    cohort_name = serializers.CharField(source='cohort.name', read_only=True)
    cohort_start_date = serializers.DateField(
        source='cohort.start_date', read_only=True
    )
    owner_id = serializers.PrimaryKeyRelatedField(
        source='owner',
        queryset=User.objects.filter(is_staff=True),
        required=False,
        allow_null=True,
        write_only=True,
    )
    user_id = serializers.PrimaryKeyRelatedField(
        source='user',
        queryset=User.objects.filter(is_staff=False),
        required=False,
        allow_null=True,
        write_only=True,
    )
    ghl_user_id = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    start_date = serializers.DateField(required=False)
    completion_percent = serializers.IntegerField(read_only=True)
    outstanding = serializers.ListField(child=serializers.CharField(), read_only=True)
    status = serializers.CharField(read_only=True)
    checklist = AgentChecklistItemSerializer(
        source='checklist_items', many=True, read_only=True
    )
    carriers = AgentCarrierSerializer(
        source='carrier_requirements', many=True, read_only=True
    )

    class Meta:
        model = OnboardingAgent
        fields = [
            'id',
            'full_name',
            'email',
            'user',
            'user_id',
            'ghl_user_id',
            'cohort',
            'cohort_name',
            'cohort_start_date',
            'start_date',
            'owner',
            'owner_id',
            'role',
            'state',
            'agent_type',
            'manually_at_risk',
            'last_updated_by',
            'completion_percent',
            'outstanding',
            'status',
            'checklist',
            'carriers',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'full_name': {'required': False, 'allow_blank': True},
        }

    def get_owner(self, obj):
        return user_brief(obj.owner)

    def get_last_updated_by(self, obj):
        return user_brief(obj.last_updated_by)

    def get_user(self, obj):
        return user_brief(obj.user)

    def _resolve_ghl_user(self, ghl_user_id):
        ghl_user_id = (ghl_user_id or '').strip()
        if not ghl_user_id:
            return None
        ghl_user = GhlUser.objects.filter(ghl_id=ghl_user_id).first()
        if ghl_user is None:
            raise serializers.ValidationError(
                {'ghl_user_id': 'No synced GoHighLevel user with that id.'}
            )
        return ghl_user

    def _fill_from_ghl(self, attrs, ghl_user):
        if not attrs.get('full_name'):
            attrs['full_name'] = (
                ghl_user.name
                or ' '.join(
                    part
                    for part in (ghl_user.first_name, ghl_user.last_name)
                    if part
                )
            )
        if not attrs.get('email') and ghl_user.email:
            attrs['email'] = ghl_user.email
        if not attrs.get('full_name'):
            raise serializers.ValidationError(
                {'full_name': 'That GoHighLevel user has no name.'}
            )
        return attrs

    def _attach_ghl(self, agent, ghl_user):
        if ghl_user is None or agent.user_id is None:
            return
        try:
            link_mirrored_user(ghl_user.ghl_id, agent.user)
        except GhlError as exc:
            raise serializers.ValidationError({'ghl_user_id': str(exc)}) from exc

    def _link_person(self, agent, *, user=None, user_in_payload=False):
        try:
            return apply_person_link(
                agent,
                email=agent.email,
                user=user,
                user_in_payload=user_in_payload,
            )
        except PersonLinkError as exc:
            raise serializers.ValidationError({exc.field: str(exc)}) from exc

    def validate(self, attrs):
        ghl_user = self._resolve_ghl_user(attrs.pop('ghl_user_id', ''))
        self._ghl_user = ghl_user
        if ghl_user is not None:
            attrs = self._fill_from_ghl(attrs, ghl_user)
        if self.instance is None and not (attrs.get('full_name') or '').strip():
            raise serializers.ValidationError(
                {'full_name': 'This field is required.'}
            )
        return attrs

    def create(self, validated):
        cohort = validated['cohort']
        validated.setdefault('start_date', cohort.start_date)
        user_in_payload = 'user' in validated
        user = validated.pop('user', None)
        agent = super().create(validated)
        agent = self._link_person(
            agent, user=user, user_in_payload=user_in_payload
        )
        self._attach_ghl(agent, getattr(self, '_ghl_user', None))
        return agent

    def update(self, instance, validated):
        user_in_payload = 'user' in validated
        user = validated.pop('user', None)
        instance = super().update(instance, validated)
        instance = self._link_person(
            instance, user=user, user_in_payload=user_in_payload
        )
        self._attach_ghl(instance, getattr(self, '_ghl_user', None))
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        extra = self.context.get('progress')
        if extra is None:
            settings = self.context.get('settings') or OnboardingSettings.load()
            extra = annotate_agent(instance, settings)
        data['completion_percent'] = extra['completion_percent']
        data['outstanding'] = extra['outstanding']
        data['status'] = extra['status']
        return data


class OnboardingAgentListSerializer(OnboardingAgentSerializer):
    class Meta(OnboardingAgentSerializer.Meta):
        fields = [
            'id',
            'full_name',
            'email',
            'user',
            'user_id',
            'cohort',
            'cohort_name',
            'cohort_start_date',
            'start_date',
            'owner',
            'owner_id',
            'role',
            'state',
            'agent_type',
            'manually_at_risk',
            'last_updated_by',
            'completion_percent',
            'outstanding',
            'status',
            'created_at',
            'updated_at',
        ]


class BulkAgentRowSerializer(serializers.Serializer):
    full_name = serializers.CharField(
        max_length=200, required=False, allow_blank=True
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    ghl_user_id = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.DateField(required=False)
    owner_id = serializers.PrimaryKeyRelatedField(
        source='owner',
        queryset=User.objects.filter(is_staff=True),
        required=False,
        allow_null=True,
    )
    role = serializers.CharField(max_length=80, required=False, allow_blank=True)
    state = serializers.CharField(max_length=80, required=False, allow_blank=True)
    agent_type = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate(self, attrs):
        ghl_id = (attrs.get('ghl_user_id') or '').strip()
        ghl_user = None
        if ghl_id:
            ghl_user = GhlUser.objects.filter(ghl_id=ghl_id).first()
            if ghl_user is None:
                raise serializers.ValidationError(
                    {'ghl_user_id': 'No synced GoHighLevel user with that id.'}
                )
            if not attrs.get('full_name'):
                attrs['full_name'] = (
                    ghl_user.name
                    or ' '.join(
                        part
                        for part in (ghl_user.first_name, ghl_user.last_name)
                        if part
                    )
                )
            if not attrs.get('email') and ghl_user.email:
                attrs['email'] = ghl_user.email
        if not (attrs.get('full_name') or '').strip():
            raise serializers.ValidationError(
                {'full_name': 'Provide a name or a GoHighLevel user.'}
            )
        attrs['_ghl_user'] = ghl_user
        return attrs


class BulkAgentSerializer(serializers.Serializer):
    cohort = serializers.PrimaryKeyRelatedField(
        queryset=Cohort.objects.all(), required=False
    )
    cohort_name = serializers.CharField(max_length=200, required=False)
    start_date = serializers.DateField(required=False)
    owner_id = serializers.PrimaryKeyRelatedField(
        source='owner',
        queryset=User.objects.filter(is_staff=True),
        required=False,
        allow_null=True,
    )
    agents = BulkAgentRowSerializer(many=True)

    def validate(self, attrs):
        if not attrs.get('cohort') and not (
            attrs.get('cohort_name') and attrs.get('start_date')
        ):
            raise serializers.ValidationError(
                'Provide a cohort id, or cohort_name and start_date to create one.'
            )
        if not attrs.get('agents'):
            raise serializers.ValidationError(
                {'agents': 'Add at least one agent.'}
            )
        return attrs


class OnboardingSettingsSerializer(serializers.ModelSerializer):
    sheet_configured = serializers.SerializerMethodField()
    sheet_tab = serializers.SerializerMethodField()
    sync_state = serializers.SerializerMethodField()

    class Meta:
        model = OnboardingSettings
        fields = [
            'at_risk_after_days',
            'overdue_after_days',
            'default_template',
            'sync_enabled',
            'sheet_configured',
            'sheet_tab',
            'sync_state',
            'updated_at',
        ]

    def get_sheet_configured(self, obj):
        from .services.sheets import credentials_configured

        return credentials_configured()

    def get_sheet_tab(self, obj):
        from django.conf import settings as django_settings

        return getattr(django_settings, 'GOOGLE_SHEETS_TAB_NAME', 'Onboarding')

    def get_sync_state(self, obj):
        state = SpreadsheetSyncState.load()
        return {
            'configured': state.configured,
            'last_success_at': state.last_success_at,
            'last_error': state.last_error,
            'last_error_at': state.last_error_at,
            'last_synced_count': state.last_synced_count,
        }


class OnboardingEventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()
    agent_name = serializers.CharField(source='agent.full_name', read_only=True)

    class Meta:
        model = OnboardingEvent
        fields = ['id', 'action', 'label', 'actor', 'agent', 'agent_name', 'created_at']

    def get_actor(self, obj):
        return user_brief(obj.actor)


class StaffUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']
