import uuid

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from onboarding.models import AgentChecklistItem, Cohort, OnboardingAgent

TOKEN = 'sync-test-token'
TEAM_A = '11111111-1111-1111-1111-111111111111'
TEAM_B = '33333333-3333-3333-3333-333333333333'
USER_A = '22222222-2222-2222-2222-222222222222'


def snapshot(agents, teams=None):
    if teams is None:
        teams = [
            {
                'id': TEAM_A,
                'name': 'Alpha',
                'active': True,
                'created_at': '2026-01-15',
            }
        ]
    return {'teams': teams, 'agents': agents}


@override_settings(DASHBOARD_SYNC_TOKEN=TOKEN)
class DashboardSyncTests(APITestCase):
    def post_snapshot(self, body, token=TOKEN):
        return self.client.post(
            '/api/onboarding/dashboard-sync/',
            body,
            format='json',
            HTTP_X_SYNC_TOKEN=token,
        )

    def test_missing_token_is_rejected(self):
        res = self.post_snapshot(snapshot([]), token='')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Cohort.objects.count(), 0)

    def test_snapshot_creates_then_updates_without_duplicating(self):
        created = self.post_snapshot(
            snapshot(
                [
                    {
                        'user_id': USER_A,
                        'name': 'Ada Agent',
                        'email': 'ada@example.com',
                        'team_id': TEAM_A,
                    }
                ]
            )
        )
        self.assertEqual(created.status_code, 200, created.data)
        self.assertEqual(created.data['agents_created'], 1)
        agent = OnboardingAgent.objects.get()
        self.assertEqual(agent.cohort.name, 'Alpha')
        self.assertEqual(agent.user.email, 'ada@example.com')
        self.assertEqual(agent.start_date.isoformat(), '2026-01-15')
        item = AgentChecklistItem.objects.create(
            agent=agent, label='Keep me', is_required=True
        )

        moved = self.post_snapshot(
            snapshot(
                [
                    {
                        'user_id': USER_A,
                        'name': 'Ada Agent',
                        'email': 'ada@example.com',
                        'team_id': TEAM_B,
                    }
                ],
                teams=[
                    {
                        'id': TEAM_A,
                        'name': 'Alpha',
                        'active': True,
                        'created_at': '2026-01-15',
                    },
                    {
                        'id': TEAM_B,
                        'name': 'Beta',
                        'active': True,
                        'created_at': '2026-02-01',
                    },
                ],
            )
        )
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(moved.data['agents_created'], 0)
        self.assertEqual(moved.data['agents_updated'], 1)
        self.assertEqual(OnboardingAgent.objects.count(), 1)
        agent.refresh_from_db()
        self.assertEqual(agent.cohort.name, 'Beta')
        self.assertEqual(agent.start_date.isoformat(), '2026-01-15')
        self.assertTrue(AgentChecklistItem.objects.filter(pk=item.pk).exists())

    def test_existing_email_is_linked_instead_of_duplicated(self):
        cohort = Cohort.objects.create(name='Old class', start_date=timezone.localdate())
        OnboardingAgent.objects.create(
            full_name='Ada Agent',
            email='ada@example.com',
            cohort=cohort,
            start_date=cohort.start_date,
        )
        res = self.post_snapshot(
            snapshot(
                [
                    {
                        'user_id': USER_A,
                        'name': 'Ada Agent',
                        'email': 'ada@example.com',
                        'team_id': TEAM_A,
                    }
                ]
            )
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['agents_created'], 0)
        self.assertEqual(OnboardingAgent.objects.count(), 1)
        agent = OnboardingAgent.objects.get()
        self.assertEqual(str(agent.dashboard_user_id), USER_A)
        self.assertEqual(agent.cohort.name, 'Alpha')

    def test_omitted_agent_stays_and_missing_team_is_archived(self):
        first = self.post_snapshot(
            snapshot(
                [
                    {
                        'user_id': USER_A,
                        'name': 'Ada Agent',
                        'email': 'ada@example.com',
                        'team_id': TEAM_A,
                    }
                ]
            )
        )
        self.assertEqual(first.status_code, 200, first.data)
        res = self.post_snapshot(
            snapshot(
                [],
                teams=[
                    {
                        'id': TEAM_B,
                        'name': 'Beta',
                        'active': True,
                        'created_at': '2026-02-01',
                    }
                ],
            )
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['agents_unassigned'], 1)
        self.assertEqual(res.data['teams_archived'], 1)
        agent = OnboardingAgent.objects.get()
        self.assertTrue(agent.dashboard_unassigned)
        self.assertEqual(agent.cohort.name, 'Alpha')
        self.assertFalse(Cohort.objects.get(name='Alpha').is_active)
        self.assertEqual(OnboardingAgent.objects.count(), 1)

    def test_agent_without_a_team_is_not_created(self):
        res = self.post_snapshot(
            snapshot(
                [
                    {
                        'user_id': USER_A,
                        'name': 'Ada Agent',
                        'email': 'ada@example.com',
                        'team_id': None,
                    }
                ]
            )
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['agents_created'], 0)
        self.assertEqual(OnboardingAgent.objects.count(), 0)
        self.assertTrue(Cohort.objects.filter(dashboard_team_id=uuid.UUID(TEAM_A)).exists())
