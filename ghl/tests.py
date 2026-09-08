from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from ghl.models import GhlToken, GhlUser
from ghl.services import apply_webhook, save_user, search_local_users

User = get_user_model()


def _token(**extra):
    fields = {
        'access_token': 'access',
        'refresh_token': 'refresh',
        'expires_at': timezone.now() + timedelta(hours=12),
        'location_id': 'loc-1',
        'company_id': 'co-1',
        'user_type': GhlToken.UserType.LOCATION,
    }
    fields.update(extra)
    return GhlToken.objects.create(**fields)


class LocalUserSearchTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            'boss', 'boss@example.com', 'pw', is_staff=True
        )
        self.client.force_authenticate(user=self.admin)
        GhlUser.objects.create(
            ghl_id='u-alex',
            name='Alex Rivera',
            first_name='Alex',
            last_name='Rivera',
            email='alex@ghl.example.com',
            role='admin',
        )
        GhlUser.objects.create(
            ghl_id='u-sam',
            name='Sam Patel',
            first_name='Sam',
            last_name='Patel',
            email='sam@ghl.example.com',
        )

    def test_search_hits_the_local_table(self):
        res = self.client.get('/api/ghl/users/search/', {'query': 'alex'})
        assert res.status_code == 200, res.data
        ids = [u['id'] for u in res.data['users']]
        assert ids == ['u-alex']
        assert res.data['users'][0]['firstName'] == 'Alex'
        assert res.data['users'][0]['email'] == 'alex@ghl.example.com'

    def test_empty_query_lists_synced_users(self):
        res = self.client.get('/api/ghl/users/search/')
        assert res.status_code == 200
        assert res.data['total'] == 2

    def test_search_does_not_call_ghl(self):
        with patch('ghl.services.search_users') as live:
            self.client.get('/api/ghl/users/search/', {'query': 'sam'})
        live.assert_not_called()

    def test_unconnected_and_empty_is_404(self):
        GhlUser.objects.all().delete()
        res = self.client.get('/api/ghl/users/search/')
        assert res.status_code == 404


class WebhookSyncTest(APITestCase):
    def test_contact_create_upserts(self):
        res = self.client.post(
            '/api/ghl/webhook/',
            {
                'type': 'ContactCreate',
                'locationId': 'loc-1',
                'id': 'ct-1',
                'email': 'john@agency.com',
                'name': 'John Deo',
                'firstName': 'John',
                'lastName': 'Deo',
                'phone': '+15550001111',
            },
            format='json',
        )
        assert res.status_code == 200, res.data
        assert res.data['action'] == 'upserted'
        row = GhlUser.objects.get(ghl_id='ct-1')
        assert row.email == 'john@agency.com'
        assert row.name == 'John Deo'
        assert row.location_id == 'loc-1'

    def test_contact_update_changes_fields(self):
        GhlUser.objects.create(
            ghl_id='ct-1', name='Old', email='old@agency.com'
        )
        res = self.client.post(
            '/api/ghl/webhook/',
            {
                'type': 'ContactUpdate',
                'locationId': 'loc-1',
                'id': 'ct-1',
                'email': 'new@agency.com',
                'name': 'New Name',
                'firstName': 'New',
                'lastName': 'Name',
            },
            format='json',
        )
        assert res.status_code == 200
        row = GhlUser.objects.get(ghl_id='ct-1')
        assert row.email == 'new@agency.com'
        assert row.name == 'New Name'

    def test_contact_delete_removes_the_row(self):
        GhlUser.objects.create(ghl_id='ct-1', name='Gone')
        res = self.client.post(
            '/api/ghl/webhook/',
            {'type': 'ContactDelete', 'locationId': 'loc-1', 'id': 'ct-1'},
            format='json',
        )
        assert res.status_code == 200
        assert res.data['action'] == 'deleted'
        assert not GhlUser.objects.filter(ghl_id='ct-1').exists()

    def test_user_create_upserts(self):
        res = self.client.post(
            '/api/ghl/webhook/',
            {
                'type': 'UserCreate',
                'locationId': 'loc-1',
                'id': 'usr-1',
                'firstName': 'Pat',
                'lastName': 'Lee',
                'email': 'pat@agency.com',
                'role': 'user',
            },
            format='json',
        )
        assert res.status_code == 200
        row = GhlUser.objects.get(ghl_id='usr-1')
        assert row.name == 'Pat Lee'
        assert row.role == 'user'
        assert row.email == 'pat@agency.com'

    def test_nested_data_payload_upserts(self):
        apply_webhook(
            {
                'type': 'ContactCreate',
                'data': {
                    'id': 'ct-nested',
                    'firstName': 'Nested',
                    'lastName': 'Person',
                    'email': 'nested@agency.com',
                },
            }
        )
        assert GhlUser.objects.get(ghl_id='ct-nested').email == (
            'nested@agency.com'
        )

    def test_unknown_type_is_ignored(self):
        res = self.client.post(
            '/api/ghl/webhook/',
            {'type': 'AppointmentCreate', 'id': 'ap-1'},
            format='json',
        )
        assert res.status_code == 200
        assert res.data['action'] == 'ignored'
        assert GhlUser.objects.count() == 0

    def test_delete_keeps_the_linked_account(self):
        person = User.objects.create_user('pat', 'pat@example.com', 'pw')
        GhlUser.objects.create(ghl_id='usr-1', name='Pat', user=person)
        apply_webhook({'type': 'UserDelete', 'id': 'usr-1'})
        assert not GhlUser.objects.filter(ghl_id='usr-1').exists()
        assert User.objects.filter(pk=person.pk).exists()


class SyncLocationUsersTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            'boss', 'boss@example.com', 'pw', is_staff=True
        )
        self.client.force_authenticate(user=self.admin)
        _token()

    def test_sync_endpoint_saves_listed_users(self):
        listed = [
            {
                'id': 'u-1',
                'name': 'Alex Rivera',
                'firstName': 'Alex',
                'lastName': 'Rivera',
                'email': 'alex@ghl.example.com',
                'roles': {'role': 'admin', 'locationIds': ['loc-1']},
            }
        ]
        with patch('ghl.services.list_location_users', return_value=listed):
            res = self.client.post('/api/ghl/users/sync/', {}, format='json')
        assert res.status_code == 200, res.data
        assert res.data['synced'] == 1
        assert GhlUser.objects.get(ghl_id='u-1').email == 'alex@ghl.example.com'

    def test_save_user_accepts_webhook_role_shape(self):
        row = save_user(
            {
                'id': 'usr-2',
                'firstName': 'Sam',
                'lastName': 'Patel',
                'email': 'sam@ghl.example.com',
                'role': 'user',
                'locationId': 'loc-1',
            }
        )
        assert row.role == 'user'
        assert row.location_id == 'loc-1'
        assert search_local_users('sam')['total'] == 1

    def test_same_ghl_user_is_stored_per_location(self):
        from ghl.services import onboard_company, save_user

        save_user(
            {'id': 'u-same', 'name': 'Alex', 'email': 'a@x.com'},
            location_id='loc-a',
        )
        save_user(
            {'id': 'u-same', 'name': 'Alex', 'email': 'a@x.com'},
            location_id='loc-b',
        )
        rows = GhlUser.objects.filter(ghl_id='u-same')
        assert rows.count() == 2
        assert set(rows.values_list('location_id', flat=True)) == {'loc-a', 'loc-b'}
        # Picker still shows the person once.
        assert search_local_users('alex')['total'] == 1

    def test_agency_onboard_mints_each_location_then_syncs_users(self):
        from ghl.services import onboard_company

        company = _token(
            location_id=None,
            user_type=GhlToken.UserType.COMPANY,
        )
        loc_users = {
            'loc-a': [
                {
                    'id': 'u-same',
                    'name': 'Alex',
                    'email': 'a@x.com',
                    'roles': {'locationIds': ['loc-a']},
                }
            ],
            'loc-b': [
                {
                    'id': 'u-same',
                    'name': 'Alex',
                    'email': 'a@x.com',
                    'roles': {'locationIds': ['loc-b']},
                }
            ],
        }

        def users_for(location_id=None, company_id=None):
            return loc_users[location_id]

        with (
            patch('ghl.services.list_installed_locations', return_value=[]),
            patch(
                'ghl.services.list_agency_locations',
                return_value=[{'id': 'loc-a'}, {'id': 'loc-b'}],
            ),
            patch(
                'ghl.services.fetch_location_token',
                side_effect=lambda *a, **k: _token(
                    location_id=a[1], user_type=GhlToken.UserType.LOCATION
                ),
            ),
            patch('ghl.services.list_location_users', side_effect=users_for),
        ):
            result = onboard_company(company)

        assert result['locations'] == 2
        assert result['users'] == 2
        assert GhlUser.objects.filter(ghl_id='u-same').count() == 2
