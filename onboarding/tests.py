from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from courses.models import Course
from onboarding.constants import (
    GROUP_ASSISTANT,
    GROUP_LEADERSHIP,
    GROUP_RECRUITER,
)
from onboarding.models import (
    Carrier,
    ChecklistItemDefinition,
    Cohort,
    OnboardingAgent,
    OnboardingSettings,
    RequirementTemplate,
    TemplateCarrier,
    TemplateChecklistItem,
)
from onboarding.services.status import (
    STATUS_AT_RISK,
    STATUS_ON_TRACK,
    STATUS_OVERDUE,
)

User = get_user_model()


class OnboardingApiTest(APITestCase):
    def setUp(self):
        self.patcher = patch('onboarding.tasks.sync_agent.delay')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        for name in (GROUP_ASSISTANT, GROUP_RECRUITER, GROUP_LEADERSHIP):
            Group.objects.get_or_create(name=name)

        self.assistant = User.objects.create_user(
            'assist', 'assist@example.com', 'pw-assist-123', is_staff=True
        )
        self.assistant.groups.add(Group.objects.get(name=GROUP_ASSISTANT))
        self.recruiter = User.objects.create_user(
            'recruit', 'recruit@example.com', 'pw-recruit-123', is_staff=True
        )
        self.recruiter.groups.add(Group.objects.get(name=GROUP_RECRUITER))
        self.lead = User.objects.create_user(
            'lead', 'lead@example.com', 'pw-lead-1234', is_staff=True
        )
        self.lead.groups.add(Group.objects.get(name=GROUP_LEADERSHIP))
        self.staff_plain = User.objects.create_user(
            'staffy', 'staffy@example.com', 'pw-staff-123', is_staff=True
        )
        self.student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'pw-pupil-123'
        )

        self.carrier = Carrier.objects.create(name='Acme Life', code='acme-life')
        self.item_def = ChecklistItemDefinition.objects.create(
            label='E&O verified', is_required=True, sort_order=0
        )
        self.item_def_2 = ChecklistItemDefinition.objects.create(
            label='Bank form', is_required=True, sort_order=1
        )
        self.template = RequirementTemplate.objects.create(
            name='Default', is_default=True
        )
        TemplateCarrier.objects.create(
            template=self.template, carrier=self.carrier, is_required=True
        )
        TemplateChecklistItem.objects.create(
            template=self.template,
            definition=self.item_def,
            is_required=True,
            sort_order=0,
        )
        TemplateChecklistItem.objects.create(
            template=self.template,
            definition=self.item_def_2,
            is_required=True,
            sort_order=1,
        )
        settings = OnboardingSettings.load()
        settings.default_template = self.template
        settings.save()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _create_agent(self, name='Jane Doe', owner=None, start=None):
        self.auth(self.assistant)
        cohort = Cohort.objects.create(
            name=f'Week {name}', start_date=start or date(2026, 8, 25)
        )
        payload = {
            'full_name': name,
            'cohort': cohort.id,
        }
        if owner:
            payload['owner_id'] = owner.id
        res = self.client.post(
            '/api/onboarding/agents/',
            payload,
            format='json',
        )
        assert res.status_code == 201, res.data
        return res.data, cohort

    def test_permissions_matrix(self):
        self.auth(self.student)
        assert self.client.get('/api/onboarding/dashboard/').status_code == 403

        self.auth(self.staff_plain)
        assert self.client.get('/api/onboarding/dashboard/').status_code == 403

        self.auth(self.lead)
        assert self.client.get('/api/onboarding/dashboard/').status_code == 200
        assert (
            self.client.post(
                '/api/onboarding/cohorts/',
                {'name': 'X', 'start_date': '2026-08-25'},
                format='json',
            ).status_code
            == 403
        )

        self.auth(self.assistant)
        me = self.client.get('/api/auth/me/')
        assert me.data['is_staff'] is True
        assert me.data['onboarding_role'] == 'assistant'

        self.auth(self.student)
        assert self.client.get('/api/auth/me/').data['onboarding_role'] is None

    def test_create_and_bulk_and_progress(self):
        data, cohort = self._create_agent()
        assert data['completion_percent'] == 0
        assert 'E&O verified' in data['outstanding']
        assert data['status'] == STATUS_ON_TRACK
        assert len(data['checklist']) == 2
        assert len(data['carriers']) == 1
        assert data['carriers'][0]['status'] == 'not_started'

        item_id = data['checklist'][0]['id']
        res = self.client.patch(
            f"/api/onboarding/agents/{data['id']}/checklist/{item_id}/",
            {'is_completed': True},
            format='json',
        )
        assert res.status_code == 200, res.data
        detail = self.client.get(f"/api/onboarding/agents/{data['id']}/")
        assert detail.data['completion_percent'] == 33

        res = self.client.patch(
            f"/api/onboarding/agents/{data['id']}/checklist/{item_id}/",
            {'is_completed': False},
            format='json',
        )
        assert res.status_code == 200
        detail = self.client.get(f"/api/onboarding/agents/{data['id']}/")
        assert detail.data['completion_percent'] == 0

        req_id = data['carriers'][0]['id']
        res = self.client.patch(
            f"/api/onboarding/agents/{data['id']}/carriers/{req_id}/",
            {'status': 'approved'},
            format='json',
        )
        assert res.status_code == 200, res.data
        detail = self.client.get(f"/api/onboarding/agents/{data['id']}/")
        assert detail.data['completion_percent'] == 33

        res = self.client.post(
            '/api/onboarding/agents/bulk/',
            {
                'cohort': cohort.id,
                'owner_id': self.recruiter.id,
                'agents': [
                    {'full_name': 'Alex One'},
                    {'full_name': 'Alex Two'},
                ],
            },
            format='json',
        )
        assert res.status_code == 201, res.data
        assert len(res.data['agents']) == 2

    def test_recruiter_can_only_edit_owned(self):
        data, _ = self._create_agent(owner=self.assistant)
        item_id = data['checklist'][0]['id']
        self.auth(self.recruiter)
        res = self.client.patch(
            f"/api/onboarding/agents/{data['id']}/checklist/{item_id}/",
            {'is_completed': True},
            format='json',
        )
        assert res.status_code == 403

        owned, _ = self._create_agent(name='Owned Agent', owner=self.recruiter)
        item_id = owned['checklist'][0]['id']
        self.auth(self.recruiter)
        res = self.client.patch(
            f"/api/onboarding/agents/{owned['id']}/checklist/{item_id}/",
            {'is_completed': True},
            format='json',
        )
        assert res.status_code == 200, res.data

        res = self.client.post(
            '/api/onboarding/agents/',
            {'full_name': 'Nope', 'cohort': owned['cohort']},
            format='json',
        )
        assert res.status_code == 403

    def test_audit_and_filters_and_history(self):
        data, cohort = self._create_agent()
        self.auth(self.assistant)
        audit = self.client.get('/api/onboarding/audit/')
        assert audit.status_code == 200
        labels = {row['label'] for row in audit.data['results']}
        assert 'E&O verified' in labels
        assert 'Acme Life' in labels

        listed = self.client.get(
            f"/api/onboarding/agents/?cohort={cohort.id}&status=on_track"
        )
        assert listed.status_code == 200
        assert listed.data['count'] >= 1

        self.client.patch(
            f"/api/onboarding/cohorts/{cohort.id}/",
            {'is_active': False},
            format='json',
        )
        still = self.client.get(f"/api/onboarding/agents/{data['id']}/")
        assert still.status_code == 200
        listed_all = self.client.get(f'/api/onboarding/agents/?cohort={cohort.id}')
        assert listed_all.data['count'] >= 1

    def test_status_flag_and_configured_thresholds(self):
        data, _ = self._create_agent()
        item_id = data['checklist'][0]['id']
        self.auth(self.assistant)
        self.client.patch(
            f"/api/onboarding/agents/{data['id']}/checklist/{item_id}/",
            {'is_flagged': True},
            format='json',
        )
        detail = self.client.get(f"/api/onboarding/agents/{data['id']}/")
        assert detail.data['status'] == STATUS_AT_RISK

        settings = OnboardingSettings.load()
        settings.overdue_after_days = 1
        settings.save()
        agent = OnboardingAgent.objects.get(pk=data['id'])
        agent.start_date = timezone.localdate() - timedelta(days=2)
        agent.save(update_fields=['start_date'])
        detail = self.client.get(f"/api/onboarding/agents/{data['id']}/")
        assert detail.data['status'] == STATUS_OVERDUE

    @override_settings(
        GOOGLE_SERVICE_ACCOUNT_FILE='',
        GOOGLE_SERVICE_ACCOUNT_JSON='',
        GOOGLE_SHEETS_SPREADSHEET_ID='',
    )
    def test_sync_not_configured(self):
        self.auth(self.assistant)
        res = self.client.post('/api/onboarding/sync/')
        assert res.status_code == 400
        assert res.data['configured'] is False
        assert 'GOOGLE' in res.data['error']

        status = self.client.get('/api/onboarding/sync/')
        assert status.status_code == 200
        assert status.data['configured'] is False

    def test_delete_catalog_items(self):
        unused = Carrier.objects.create(name='Spare Co', code='spare-co')
        self.auth(self.assistant)
        res = self.client.delete(f'/api/onboarding/carriers/{unused.id}/')
        assert res.status_code == 204
        assert not Carrier.objects.filter(id=unused.id).exists()

        data, _ = self._create_agent()
        in_use = Carrier.objects.get(id=data['carriers'][0]['carrier'])
        blocked = self.client.delete(f'/api/onboarding/carriers/{in_use.id}/')
        assert blocked.status_code == 400
        assert 'still have this carrier' in blocked.data['detail']
        assert Carrier.objects.filter(id=in_use.id).exists()

        def_id = self.item_def_2.id
        gone = self.client.delete(f'/api/onboarding/checklist-definitions/{def_id}/')
        assert gone.status_code == 204
        assert not ChecklistItemDefinition.objects.filter(id=def_id).exists()

        self.auth(self.recruiter)
        leftover = Carrier.objects.create(name='No Touch', code='no-touch')
        assert (
            self.client.delete(f'/api/onboarding/carriers/{leftover.id}/').status_code
            == 403
        )

    def test_edit_and_delete_agent(self):
        data, _ = self._create_agent()
        agent_id = data['id']
        self.auth(self.assistant)
        patched = self.client.patch(
            f'/api/onboarding/agents/{agent_id}/',
            {'full_name': 'Jane Updated'},
            format='json',
        )
        assert patched.status_code == 200, patched.data
        assert patched.data['full_name'] == 'Jane Updated'

        self.auth(self.recruiter)
        assert (
            self.client.delete(f'/api/onboarding/agents/{agent_id}/').status_code
            == 403
        )

        self.auth(self.assistant)
        gone = self.client.delete(f'/api/onboarding/agents/{agent_id}/')
        assert gone.status_code == 204
        assert self.client.get(f'/api/onboarding/agents/{agent_id}/').status_code == 404

    def test_edit_and_delete_cohort(self):
        data, cohort = self._create_agent()
        self.auth(self.assistant)
        patched = self.client.patch(
            f'/api/onboarding/cohorts/{cohort.id}/',
            {'name': 'Renamed Week'},
            format='json',
        )
        assert patched.status_code == 200, patched.data
        assert patched.data['name'] == 'Renamed Week'

        self.auth(self.recruiter)
        assert (
            self.client.delete(f'/api/onboarding/cohorts/{cohort.id}/').status_code
            == 403
        )

        self.auth(self.assistant)
        gone = self.client.delete(f'/api/onboarding/cohorts/{cohort.id}/')
        assert gone.status_code == 204
        assert not Cohort.objects.filter(id=cohort.id).exists()
        assert not OnboardingAgent.objects.filter(id=data['id']).exists()

    def test_lms_still_works(self):
        Course.objects.create(id='demo', title='Demo', description='x')
        self.auth(self.student)
        res = self.client.get('/api/courses/')
        assert res.status_code == 200
        self.auth(self.assistant)
        res = self.client.get('/api/auth/students/')
        assert res.status_code == 200
        assert self.client.get('/api/auth/me/').data['is_staff'] is True
