from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Course, Enrollment, Lesson

User = get_user_model()


class CatalogGatingTest(APITestCase):
    """A student sees only their assigned courses; staff see the catalog and
    are the only ones who can change it."""

    def setUp(self):
        self.admin = User.objects.create_user(
            'boss', 'boss@example.com', 'sup3r-secret-pw', is_staff=True
        )
        self.student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        self.assigned = Course.objects.create(id='assigned', title='Assigned')
        self.other = Course.objects.create(id='other', title='Not Assigned')
        for course in (self.assigned, self.other):
            Lesson.objects.create(course=course, slug='intro', title='Intro')
        Enrollment.objects.create(
            user=self.student, course=self.assigned, assigned_by=self.admin
        )

    def test_student_lists_only_assigned_courses(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get('/api/courses/')
        assert [c['id'] for c in res.data['results']] == ['assigned'], res.data

    def test_staff_lists_everything(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/courses/')
        assert {c['id'] for c in res.data['results']} == {'assigned', 'other'}

    def test_unassigned_course_is_not_reachable(self):
        self.client.force_authenticate(user=self.student)
        assert self.client.get('/api/courses/assigned/').status_code == 200
        assert self.client.get('/api/courses/other/').status_code == 404
        assert self.client.get('/api/courses/other/lessons/').status_code == 404
        assert (
            self.client.get('/api/courses/other/lessons/intro/').status_code == 404
        )
        assert (
            self.client.post(
                '/api/courses/other/lessons/intro/complete/'
            ).status_code
            == 404
        )

    def test_assigned_course_lessons_still_work(self):
        self.client.force_authenticate(user=self.student)
        assert self.client.get('/api/courses/assigned/lessons/').status_code == 200
        res = self.client.post('/api/courses/assigned/lessons/intro/complete/')
        assert res.status_code == 201, res.data
        res = self.client.get('/api/me/completions/')
        assert [row['course'] for row in res.data] == ['assigned']

    def test_students_cannot_change_the_catalog(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(
            '/api/courses/', {'title': 'Mine', 'description': ''}, format='json'
        )
        assert res.status_code == 403, res.data
        assert self.client.delete('/api/courses/assigned/').status_code == 403
        assert (
            self.client.patch(
                '/api/courses/assigned/', {'title': 'Renamed'}, format='json'
            ).status_code
            == 403
        )

    def test_staff_can_change_the_catalog(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            '/api/courses/',
            {'title': 'Brand New', 'description': ''},
            format='json',
        )
        assert res.status_code == 201, res.data
        assert Course.objects.filter(title='Brand New').exists()

    def test_anonymous_access_is_rejected(self):
        assert self.client.get('/api/courses/').status_code == 401
        assert self.client.get('/api/courses/assigned/').status_code == 401

    def test_enrollment_survives_deleting_the_assigning_admin(self):
        self.admin.delete()
        enrollment = Enrollment.objects.get(user=self.student)
        assert enrollment.assigned_by is None
        assert enrollment.course_id == 'assigned'
