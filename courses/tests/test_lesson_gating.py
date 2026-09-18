from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from ..models import Course, Enrollment, Lesson, LessonCompletion

User = get_user_model()


class OrderCompletionGateTest(APITestCase):
    """A lesson can't be marked complete until every earlier lesson (by
    `Lesson.order`) in the same course is already completed."""

    def setUp(self):
        self.admin = User.objects.create_user(
            'boss', 'boss@example.com', 'sup3r-secret-pw', is_staff=True
        )
        self.student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        self.course = Course.objects.create(id='course', title='Course')
        self.lessons = [
            Lesson.objects.create(
                course=self.course, slug=f'lesson-{i}', title=f'Lesson {i}', order=i
            )
            for i in range(3)
        ]
        Enrollment.objects.create(
            user=self.student, course=self.course, assigned_by=self.admin
        )
        self.client.force_authenticate(user=self.student)

    def complete(self, index):
        return self.client.post(
            f'/api/courses/course/lessons/lesson-{index}/complete/'
        )

    def test_first_lesson_has_no_earlier_lessons_to_block_it(self):
        assert self.complete(0).status_code == 201

    def test_skipping_ahead_is_rejected(self):
        res = self.complete(1)
        assert res.status_code == 400, res.data
        assert not LessonCompletion.objects.filter(
            user=self.student, lesson=self.lessons[1]
        ).exists()

    def test_completing_in_order_succeeds(self):
        assert self.complete(0).status_code == 201
        assert self.complete(1).status_code == 201
        assert self.complete(2).status_code == 201

    def test_leapfrogging_is_rejected_even_when_the_immediate_prior_is_done(self):
        assert self.complete(0).status_code == 201
        res = self.complete(2)
        assert res.status_code == 400, res.data

    def test_grandfathered_out_of_order_completion_is_not_re_blocked(self):
        # Simulate a completion that predates the order gate (or was created
        # directly, bypassing the API) — lesson 1 done, lesson 0 is not.
        LessonCompletion.objects.create(user=self.student, lesson=self.lessons[1])
        res = self.complete(1)
        assert res.status_code == 200, res.data

    def test_unmarking_and_the_gate_reapplies(self):
        assert self.complete(0).status_code == 201
        assert self.complete(1).status_code == 201
        self.client.delete('/api/courses/course/lessons/lesson-1/complete/')
        res = self.complete(1)
        assert res.status_code == 201, res.data


class LastVisitedLessonTest(APITestCase):
    """Enrollment.last_visited_lesson tracks the student's in-progress lesson
    so the sidebar's "currently open" exception survives a page refresh."""

    def setUp(self):
        self.admin = User.objects.create_user(
            'boss', 'boss@example.com', 'sup3r-secret-pw', is_staff=True
        )
        self.student = User.objects.create_user(
            'pupil', 'pupil@example.com', 'tempPass!2026'
        )
        self.course = Course.objects.create(id='course', title='Course')
        self.lessons = [
            Lesson.objects.create(
                course=self.course, slug=f'lesson-{i}', title=f'Lesson {i}', order=i
            )
            for i in range(2)
        ]
        Enrollment.objects.create(
            user=self.student, course=self.course, assigned_by=self.admin
        )

    def test_viewing_a_lesson_records_it_as_last_visited(self):
        self.client.force_authenticate(user=self.student)
        assert self.client.get('/api/courses/course/lessons/lesson-1/').status_code == 200
        enrollment = Enrollment.objects.get(user=self.student, course=self.course)
        assert enrollment.last_visited_lesson_id == self.lessons[1].id

    def test_course_serializer_exposes_last_visited_lesson_slug(self):
        self.client.force_authenticate(user=self.student)
        self.client.get('/api/courses/course/lessons/lesson-0/')
        res = self.client.get('/api/courses/course/')
        assert res.data['last_visited_lesson'] == 'lesson-0'

    def test_no_visit_yet_reports_none(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get('/api/courses/course/')
        assert res.data['last_visited_lesson'] is None

    def test_staff_viewing_does_not_touch_a_student_enrollment(self):
        self.client.force_authenticate(user=self.admin)
        assert self.client.get('/api/courses/course/lessons/lesson-1/').status_code == 200
        enrollment = Enrollment.objects.get(user=self.student, course=self.course)
        assert enrollment.last_visited_lesson_id is None
