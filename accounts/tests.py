from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from courses.models import Course, Enrollment

User = get_user_model()


class StudentApiSmokeTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            'boss', 'boss@example.com', 'sup3r-secret-pw', is_staff=True
        )

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def test_create_list_update_delete(self):
        self.auth(self.admin)
        res = self.client.post(
            '/api/auth/students/',
            {
                'username': 'alex-rivera',
                'email': 'alex@example.com',
                'first_name': 'Alex',
                'last_name': 'Rivera',
                'password': 'tempPass!2026',
            },
            format='json',
        )
        assert res.status_code == 201, res.data
        assert 'password' not in res.data
        student = User.objects.get(username='alex-rivera')
        assert student.is_staff is False
        assert student.check_password('tempPass!2026')

        res = self.client.get('/api/auth/students/')
        assert res.status_code == 200, res.data
        rows = res.data['results']
        assert [r['username'] for r in rows] == ['alex-rivera'], rows

        res = self.client.get('/api/auth/students/?search=rivera')
        assert len(res.data['results']) == 1
        res = self.client.get('/api/auth/students/?search=nobody')
        assert len(res.data['results']) == 0

        res = self.client.patch(
            f'/api/auth/students/{student.id}/',
            {'is_active': False},
            format='json',
        )
        assert res.status_code == 200, res.data
        student.refresh_from_db()
        assert student.is_active is False
        # Password untouched by a patch that omits it.
        assert student.check_password('tempPass!2026')

        res = self.client.patch(
            f'/api/auth/students/{student.id}/',
            {'password': 'newPass!2026x'},
            format='json',
        )
        assert res.status_code == 200, res.data
        student.refresh_from_db()
        assert student.check_password('newPass!2026x')

        res = self.client.delete(f'/api/auth/students/{student.id}/')
        assert res.status_code == 204
        assert not User.objects.filter(username='alex-rivera').exists()

    def test_validation(self):
        self.auth(self.admin)
        res = self.client.post(
            '/api/auth/students/',
            {'username': 'nopass', 'email': 'a@b.com'},
            format='json',
        )
        assert res.status_code == 400 and 'password' in res.data, res.data

        res = self.client.post(
            '/api/auth/students/',
            {'username': 'weak', 'email': 'a@b.com', 'password': '123'},
            format='json',
        )
        assert res.status_code == 400 and 'password' in res.data, res.data

        res = self.client.post(
            '/api/auth/students/',
            {'username': 'noemail', 'password': 'tempPass!2026'},
            format='json',
        )
        assert res.status_code == 400 and 'email' in res.data, res.data

        self.client.post(
            '/api/auth/students/',
            {
                'username': 'dupe',
                'email': 'd@e.com',
                'password': 'tempPass!2026',
            },
            format='json',
        )
        res = self.client.post(
            '/api/auth/students/',
            {
                'username': 'dupe',
                'email': 'd2@e.com',
                'password': 'tempPass!2026',
            },
            format='json',
        )
        assert res.status_code == 400 and 'username' in res.data, res.data

    def test_non_staff_and_anonymous_are_blocked(self):
        student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        self.auth(student)
        assert self.client.get('/api/auth/students/').status_code == 403
        assert (
            self.client.post(
                '/api/auth/students/',
                {
                    'username': 'x',
                    'email': 'x@y.com',
                    'password': 'tempPass!2026',
                },
                format='json',
            ).status_code
            == 403
        )

        self.client.force_authenticate(user=None)
        assert self.client.get('/api/auth/students/').status_code == 401

    def test_staff_are_not_listed_as_students(self):
        self.auth(self.admin)
        User.objects.create_user('pupil', 'pupil@example.com', 'tempPass!2026')
        res = self.client.get('/api/auth/students/')
        assert [r['username'] for r in res.data['results']] == ['pupil']

    def test_course_assignment_round_trip(self):
        student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        ux = Course.objects.create(id='ux-research', title='UX Research')
        ds = Course.objects.create(id='design-systems', title='Design Systems')
        Course.objects.create(id='motion', title='Motion Basics')
        url = f'/api/auth/students/{student.id}/courses/'

        self.auth(self.admin)
        assert self.client.get(url).data == []

        res = self.client.put(
            url, {'courses': [ux.pk, ds.pk]}, format='json'
        )
        assert res.status_code == 200, res.data
        assert {row['course'] for row in res.data} == {'ux-research', 'design-systems'}
        assert res.data[0]['assigned_by'] == 'boss'
        assert res.data[0]['title'] in ('UX Research', 'Design Systems')
        assert student.enrollments.count() == 2

        # Replacing the set drops what's missing and keeps the rest untouched.
        stamped = Enrollment.objects.get(user=student, course=ux).assigned_at
        res = self.client.put(url, {'courses': [ux.pk]}, format='json')
        assert res.status_code == 200, res.data
        assert [row['course'] for row in res.data] == ['ux-research']
        assert Enrollment.objects.get(user=student, course=ux).assigned_at == stamped

        # Empty list unassigns everything.
        res = self.client.put(url, {'courses': []}, format='json')
        assert res.data == []
        assert student.enrollments.count() == 0

    def test_assignment_validation_and_permissions(self):
        student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        Course.objects.create(id='ux-research', title='UX Research')
        url = f'/api/auth/students/{student.id}/courses/'

        self.auth(self.admin)
        res = self.client.put(url, {'courses': ['nope']}, format='json')
        assert res.status_code == 400, res.data
        res = self.client.put(url, {}, format='json')
        assert res.status_code == 400 and 'courses' in res.data, res.data
        # Duplicates collapse rather than blowing up on the unique constraint.
        res = self.client.put(
            url, {'courses': ['ux-research', 'ux-research']}, format='json'
        )
        assert res.status_code == 200 and len(res.data) == 1, res.data

        self.auth(student)
        assert self.client.get(url).status_code == 403
        assert (
            self.client.put(url, {'courses': []}, format='json').status_code == 403
        )

    def test_me_exposes_is_staff(self):
        self.auth(self.admin)
        res = self.client.get('/api/auth/me/')
        assert res.data['is_staff'] is True

        student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        self.auth(student)
        assert self.client.get('/api/auth/me/').data['is_staff'] is False


class EmailLoginTest(APITestCase):
    def setUp(self):
        User.objects.create_user(
            'alex-rivera', 'Alex@Example.com', 'tempPass!2026'
        )

    def login(self, **body):
        return self.client.post('/api/auth/login/', body, format='json')

    def test_login_with_email_returns_a_working_token(self):
        # Matched case-insensitively, as the stored email is mixed-case.
        res = self.login(email='alex@example.com', password='tempPass!2026')
        assert res.status_code == 200, res.data
        assert 'refresh' in res.data

        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}'
        )
        me = self.client.get('/api/auth/me/')
        assert me.status_code == 200, me.data
        assert me.data['username'] == 'alex-rivera'

    def test_login_reports_admin_alongside_the_tokens(self):
        res = self.login(email='alex@example.com', password='tempPass!2026')
        assert res.data['is_admin'] is False, res.data

        User.objects.create_user(
            'boss', 'boss@example.com', 'sup3r-secret-pw', is_staff=True
        )
        res = self.login(email='boss@example.com', password='sup3r-secret-pw')
        assert res.data['is_admin'] is True, res.data
        # …and in the token itself, for anything that only holds the token.
        claims = AccessToken(res.data['access'])
        assert claims['is_admin'] is True

    def test_username_is_no_longer_accepted(self):
        res = self.login(username='alex-rivera', password='tempPass!2026')
        assert res.status_code == 400, res.data

    def test_bad_credentials(self):
        assert self.login(
            email='alex@example.com', password='wrong'
        ).status_code == 401
        assert self.login(
            email='ghost@example.com', password='tempPass!2026'
        ).status_code == 401

    def test_deactivated_student_cannot_log_in(self):
        User.objects.filter(email__iexact='alex@example.com').update(
            is_active=False
        )
        assert self.login(
            email='alex@example.com', password='tempPass!2026'
        ).status_code == 401

    def test_email_must_be_unique_across_accounts(self):
        admin = User.objects.create_user(
            'boss2', 'boss2@example.com', 'sup3r-secret-pw', is_staff=True
        )
        self.client.force_authenticate(user=admin)
        res = self.client.post(
            '/api/auth/students/',
            {
                'username': 'someone-else',
                'email': 'ALEX@example.com',
                'password': 'tempPass!2026',
            },
            format='json',
        )
        assert res.status_code == 400, res.data
        assert 'email' in res.data, res.data
