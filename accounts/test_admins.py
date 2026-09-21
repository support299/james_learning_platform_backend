from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()
URL = '/api/auth/admins/'


class AdminUserManagementTest(APITestCase):
    def setUp(self):
        self.root = User.objects.create_user(
            'root', 'root@example.com', 'sup3r-secret-pw',
            is_staff=True, is_superuser=True,
        )
        self.staff = User.objects.create_user(
            'staffer', 'staffer@example.com', 'sup3r-secret-pw', is_staff=True
        )
        self.student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )

    def as_(self, user):
        self.client.force_authenticate(user=user)

    def test_only_superusers_can_reach_it(self):
        assert self.client.get(URL).status_code == 401
        self.as_(self.student)
        assert self.client.get(URL).status_code == 403
        self.as_(self.staff)
        assert self.client.get(URL).status_code == 403
        self.as_(self.root)
        assert self.client.get(URL).status_code == 200

    def test_lists_staff_only_superusers_first(self):
        self.as_(self.root)
        res = self.client.get(URL)
        assert [u['username'] for u in res.data['results']] == ['root', 'staffer']

    def test_create_makes_a_staff_account(self):
        self.as_(self.root)
        res = self.client.post(
            URL,
            {'username': 'newbie', 'email': 'newbie@example.com',
             'password': 'tempPass!2026'},
            format='json',
        )
        assert res.status_code == 201, res.data
        user = User.objects.get(username='newbie')
        assert user.is_staff and not user.is_superuser
        assert user.check_password('tempPass!2026')

    def test_create_requires_a_password(self):
        self.as_(self.root)
        res = self.client.post(
            URL, {'username': 'x', 'email': 'x@example.com'}, format='json'
        )
        assert res.status_code == 400

    def test_promote_to_superuser_keeps_staff(self):
        self.as_(self.root)
        res = self.client.patch(
            f'{URL}{self.staff.pk}/', {'is_superuser': True}, format='json'
        )
        assert res.status_code == 200, res.data
        self.staff.refresh_from_db()
        assert self.staff.is_superuser and self.staff.is_staff

    def test_promote_existing_student_to_staff(self):
        self.as_(self.root)
        res = self.client.post(
            f'{URL}promote/', {'user_id': self.student.pk}, format='json'
        )
        assert res.status_code == 200, res.data
        self.student.refresh_from_db()
        assert self.student.is_staff and not self.student.is_superuser

    def test_promote_rejects_existing_staff_and_unknown_ids(self):
        self.as_(self.root)
        res = self.client.post(
            f'{URL}promote/', {'user_id': self.staff.pk}, format='json'
        )
        assert res.status_code == 400
        for bad in (99999, 'abc', None):
            res = self.client.post(
                f'{URL}promote/', {'user_id': bad}, format='json'
            )
            assert res.status_code == 404, bad

    def test_demote_staff_back_to_student(self):
        self.as_(self.root)
        res = self.client.post(f'{URL}{self.staff.pk}/demote/')
        assert res.status_code == 204
        self.staff.refresh_from_db()
        assert not self.staff.is_staff

    def test_demote_refuses_a_superuser(self):
        other = User.objects.create_user(
            'root2', 'root2@example.com', 'pw-root2-123',
            is_staff=True, is_superuser=True,
        )
        self.as_(self.root)
        res = self.client.post(f'{URL}{other.pk}/demote/')
        assert res.status_code == 400
        other.refresh_from_db()
        assert other.is_staff and other.is_superuser

    def test_cannot_change_own_superuser_or_active_status(self):
        self.as_(self.root)
        for body in ({'is_superuser': False}, {'is_active': False}):
            res = self.client.patch(
                f'{URL}{self.root.pk}/', body, format='json'
            )
            assert res.status_code == 400, body
        self.root.refresh_from_db()
        assert self.root.is_superuser and self.root.is_active

    def test_can_still_edit_own_details(self):
        self.as_(self.root)
        res = self.client.patch(
            f'{URL}{self.root.pk}/',
            {'first_name': 'Rita', 'is_superuser': True},
            format='json',
        )
        assert res.status_code == 200, res.data

    def test_cannot_delete_or_demote_self(self):
        self.as_(self.root)
        assert self.client.delete(f'{URL}{self.root.pk}/').status_code == 400
        assert self.client.post(f'{URL}{self.root.pk}/demote/').status_code == 400
        assert User.objects.filter(pk=self.root.pk, is_staff=True).exists()

    def test_can_delete_another_admin(self):
        self.as_(self.root)
        assert self.client.delete(f'{URL}{self.staff.pk}/').status_code == 204
        assert not User.objects.filter(pk=self.staff.pk).exists()

    def test_me_and_login_expose_superuser_flag(self):
        self.as_(self.root)
        assert self.client.get('/api/auth/me/').data['is_superuser'] is True
        self.client.force_authenticate(user=None)
        res = self.client.post(
            '/api/auth/login/',
            {'email': 'root@example.com', 'password': 'sup3r-secret-pw'},
            format='json',
        )
        assert res.data['is_superadmin'] is True
        res = self.client.post(
            '/api/auth/login/',
            {'email': 'staffer@example.com', 'password': 'sup3r-secret-pw'},
            format='json',
        )
        assert res.data['is_superadmin'] is False
