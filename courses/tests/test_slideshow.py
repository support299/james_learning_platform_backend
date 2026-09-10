import os
import tempfile
import shutil
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from ..models import Course, Enrollment, Lesson, SlideshowSlide, SlideshowSlideVisit
from ..serializers import LessonSerializer, SlideshowSlideSerializer
from ..tasks import import_slideshow_pptx

User = get_user_model()

PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
    '53de0000000c4944415478da6360000002000155bf6ff30000000049454e44ae426082'
)

_MEDIA_ROOT = tempfile.mkdtemp(prefix='slideshow-test-media-')


class _BrokenClickActionShape:
    """A shape whose click_action raises KeyError, like the real
    LibreOffice-export quirk the task defends against — a dedicated class
    rather than a MagicMock subclass, so patching this one instance's
    behavior can't leak onto the shared MagicMock class other shapes use."""

    @property
    def click_action(self):
        raise KeyError('bad rel')


def make_image(name='slide.png'):
    return SimpleUploadedFile(name, PNG_BYTES, content_type='image/png')


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class SlideshowModelTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.course = Course.objects.create(id='c1', title='C1')
        self.lesson = Lesson.objects.create(
            course=self.course, slug='s1', title='S1', lesson_type=Lesson.Type.SLIDESHOW
        )

    def test_slide_defaults_to_required(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        assert slide.is_required is True
        assert slide.hotspots == []

    def test_order_unique_per_lesson(self):
        SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        with self.assertRaises(IntegrityError):
            SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())

    def test_delete_removes_image_file_from_disk(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        path = slide.image.path
        assert os.path.exists(path)
        slide.delete()
        assert not os.path.exists(path)

    def test_cascade_delete_removes_image_files(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        path = slide.image.path
        assert os.path.exists(path)
        self.lesson.delete()
        assert not os.path.exists(path)

    def test_bulk_queryset_delete_removes_image_files(self):
        s1 = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image('a.png'))
        s2 = SlideshowSlide.objects.create(lesson=self.lesson, order=1, image=make_image('b.png'))
        paths = [s1.image.path, s2.image.path]
        self.lesson.slideshow_slides.all().delete()
        for p in paths:
            assert not os.path.exists(p)

    def test_visit_unique_per_user_and_slide(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        user = User.objects.create_user('u1', 'u1@example.com', 'pw')
        SlideshowSlideVisit.objects.create(user=user, slide=slide)
        with self.assertRaises(IntegrityError):
            SlideshowSlideVisit.objects.create(user=user, slide=slide)

    def test_str_methods(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        user = User.objects.create_user('u2', 'u2@example.com', 'pw')
        visit = SlideshowSlideVisit.objects.create(user=user, slide=slide)
        assert str(slide) == f'{self.lesson.id} / slide 0'
        assert str(visit) == f'{user} ⏵ {slide.id}'


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class SlideshowSlideSerializerImageTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.course = Course.objects.create(id='img-course', title='ImgCourse')
        self.lesson = Lesson.objects.create(
            course=self.course, slug='img-show', title='ImgShow',
            lesson_type=Lesson.Type.SLIDESHOW,
        )

    def test_no_image_returns_none(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image='')
        data = SlideshowSlideSerializer(slide, context={}).data
        assert data['image'] is None

    def test_absolute_url_with_request_context(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        factory_request = type('R', (), {'build_absolute_uri': lambda self, url: f'http://testserver{url}'})()
        data = SlideshowSlideSerializer(slide, context={'request': factory_request}).data
        assert data['image'].startswith('http://testserver')

    def test_relative_url_without_request_context(self):
        slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        data = SlideshowSlideSerializer(slide, context={}).data
        assert data['image'] == slide.image.url


class WriteSlidesSerializerTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.course = Course.objects.create(id='c2', title='C2')
        self.lesson = Lesson.objects.create(
            course=self.course, slug='s2', title='S2', lesson_type=Lesson.Type.SLIDESHOW
        )
        self.s1 = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image('a.png'))
        self.s2 = SlideshowSlide.objects.create(lesson=self.lesson, order=1, image=make_image('b.png'))

    def _update(self, slides):
        serializer = LessonSerializer(
            self.lesson, data={'slides': slides}, partial=True, context={}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

    def test_save_with_title_and_overview_alongside_slides(self):
        serializer = LessonSerializer(
            self.lesson,
            data={
                'title': 'Renamed',
                'type': 'slideshow',
                'overview': 'New overview',
                'slides': [{'id': self.s1.id, 'order': 0, 'hotspots': []}],
            },
            partial=True,
            context={},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.lesson.refresh_from_db()
        assert self.lesson.title == 'Renamed'
        assert self.lesson.overview == 'New overview'

    def test_updates_order_hotspots_required(self):
        self._update([
            {'id': self.s1.id, 'order': 2, 'hotspots': [{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': self.s2.id}], 'is_required': False},
        ])
        self.s1.refresh_from_db()
        assert self.s1.order == 2
        assert self.s1.hotspots[0]['target'] == self.s2.id
        assert self.s1.is_required is False

    def test_omitted_slide_is_untouched(self):
        self._update([{'id': self.s1.id, 'order': 5, 'hotspots': []}])
        self.s2.refresh_from_db()
        assert self.s2.order == 1

    def test_unknown_slide_id_raises(self):
        from rest_framework import serializers as drf_serializers
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([{'id': 999999, 'order': 0, 'hotspots': []}])

    def test_hotspot_target_outside_lesson_raises(self):
        from rest_framework import serializers as drf_serializers
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([
                {'id': self.s1.id, 'order': 0, 'hotspots': [{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': 999999}]},
            ])

    def test_hotspot_coordinate_out_of_range_raises(self):
        from rest_framework import serializers as drf_serializers
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([
                {'id': self.s1.id, 'order': 0, 'hotspots': [{'x': 1.5, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': self.s2.id}]},
            ])

    def test_create_discards_slides_payload(self):
        lesson2 = Lesson.objects.create(
            course=self.course, slug='s2b', title='S2b', lesson_type=Lesson.Type.SLIDESHOW
        )
        serializer = LessonSerializer(
            data={'title': 'X', 'type': 'slideshow', 'slides': [{'id': 1, 'order': 0, 'hotspots': []}]},
            context={},
        )
        serializer.is_valid(raise_exception=True)
        obj = serializer.save(course=self.course, slug='fresh')
        assert obj.slideshow_slides.count() == 0


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class SlideshowSlideEndpointsTest(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('admin1', 'admin1@example.com', 'pw', is_staff=True)
        self.student = User.objects.create_user('student1', 'student1@example.com', 'pw')
        self.course = Course.objects.create(id='course1', title='Course1')
        Enrollment.objects.create(user=self.student, course=self.course, assigned_by=self.admin)
        self.lesson = Lesson.objects.create(
            course=self.course, slug='show1', title='Show1', lesson_type=Lesson.Type.SLIDESHOW
        )
        self.text_lesson = Lesson.objects.create(
            course=self.course, slug='text1', title='Text1', lesson_type=Lesson.Type.TEXT
        )
        self.slides_url = f'/api/courses/{self.course.id}/lessons/{self.lesson.slug}/slides/'

    def test_student_cannot_add_slide(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.slides_url, {'image': make_image()}, format='multipart')
        assert res.status_code == 403

    def test_missing_image_is_400(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.slides_url, {}, format='multipart')
        assert res.status_code == 400

    def test_add_slide_assigns_sequential_order(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.slides_url, {'image': make_image('a.png')}, format='multipart')
        assert res.status_code == 201
        assert res.data['order'] == 0
        res2 = self.client.post(self.slides_url, {'image': make_image('b.png')}, format='multipart')
        assert res2.data['order'] == 1

    def test_add_slide_after_deleting_middle_does_not_collide(self):
        self.client.force_authenticate(user=self.admin)
        ids = []
        for name in ('a.png', 'b.png', 'c.png'):
            res = self.client.post(self.slides_url, {'image': make_image(name)}, format='multipart')
            ids.append(res.data['id'])
        detail = f'{self.slides_url}{ids[1]}/'
        assert self.client.delete(detail).status_code == 204
        res = self.client.post(self.slides_url, {'image': make_image('d.png')}, format='multipart')
        assert res.status_code == 201
        assert res.data['order'] == 3

    def test_add_slide_rejects_non_slideshow_lesson(self):
        self.client.force_authenticate(user=self.admin)
        url = f'/api/courses/{self.course.id}/lessons/{self.text_lesson.slug}/slides/'
        res = self.client.post(url, {'image': make_image()}, format='multipart')
        assert res.status_code == 404

    def test_replace_image_deletes_old_file(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.slides_url, {'image': make_image('a.png')}, format='multipart')
        slide_id = res.data['id']
        old_path = SlideshowSlide.objects.get(pk=slide_id).image.path
        assert os.path.exists(old_path)
        detail = f'{self.slides_url}{slide_id}/'
        res2 = self.client.patch(detail, {'image': make_image('new.png')}, format='multipart')
        assert res2.status_code == 200
        assert not os.path.exists(old_path)

    def test_replace_image_missing_file_is_400(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.slides_url, {'image': make_image()}, format='multipart')
        detail = f'{self.slides_url}{res.data["id"]}/'
        assert self.client.patch(detail, {}, format='multipart').status_code == 400

    def test_delete_slide_removes_file(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.slides_url, {'image': make_image()}, format='multipart')
        slide_id = res.data['id']
        path = SlideshowSlide.objects.get(pk=slide_id).image.path
        detail = f'{self.slides_url}{slide_id}/'
        assert self.client.delete(detail).status_code == 204
        assert not os.path.exists(path)
        assert not SlideshowSlide.objects.filter(pk=slide_id).exists()

    def test_slide_endpoints_404_for_nonexistent_course(self):
        # IsStaffOrReadOnly denies a non-staff POST before the lesson lookup
        # even runs (403, tested separately) — use staff here so the request
        # actually reaches get_slideshow_lesson's visibility/existence check.
        self.client.force_authenticate(user=self.admin)
        url = '/api/courses/does-not-exist/lessons/show1/slides/'
        assert self.client.post(url, {'image': make_image()}, format='multipart').status_code == 404

    def test_visit_endpoint_404_for_unassigned_course(self):
        other_course = Course.objects.create(id='other-course', title='Other')
        other_lesson = Lesson.objects.create(
            course=other_course, slug='show2', title='Show2', lesson_type=Lesson.Type.SLIDESHOW
        )
        self.client.force_authenticate(user=self.student)
        url = f'/api/courses/{other_course.id}/lessons/{other_lesson.slug}/slide-visits/'
        assert self.client.get(url).status_code == 404


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class SlideshowVisitEndpointTest(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('admin2', 'admin2@example.com', 'pw', is_staff=True)
        self.student = User.objects.create_user('student2', 'student2@example.com', 'pw')
        self.course = Course.objects.create(id='course2', title='Course2')
        Enrollment.objects.create(user=self.student, course=self.course, assigned_by=self.admin)
        self.lesson = Lesson.objects.create(
            course=self.course, slug='show3', title='Show3', lesson_type=Lesson.Type.SLIDESHOW
        )
        self.slide = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        self.visits_url = f'/api/courses/{self.course.id}/lessons/{self.lesson.slug}/slide-visits/'

    def test_get_starts_empty(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get(self.visits_url)
        assert res.status_code == 200
        assert res.data['visited'] == []

    def test_post_marks_visited_and_is_idempotent(self):
        self.client.force_authenticate(user=self.student)
        assert self.client.post(self.visits_url, {'slide': self.slide.id}, format='json').status_code == 204
        assert self.client.post(self.visits_url, {'slide': self.slide.id}, format='json').status_code == 204
        assert SlideshowSlideVisit.objects.filter(user=self.student, slide=self.slide).count() == 1
        res = self.client.get(self.visits_url)
        assert res.data['visited'] == [self.slide.id]

    def test_post_unknown_slide_is_404(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.visits_url, {'slide': 999999}, format='json')
        assert res.status_code == 404


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ImportPptxEndpointTest(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('admin3', 'admin3@example.com', 'pw', is_staff=True)
        self.course = Course.objects.create(id='course3', title='Course3')
        self.lesson = Lesson.objects.create(
            course=self.course, slug='show4', title='Show4', lesson_type=Lesson.Type.SLIDESHOW
        )
        self.import_url = f'/api/courses/{self.course.id}/lessons/{self.lesson.slug}/import-pptx/'

    def _pptx_file(self, name='x.pptx'):
        return SimpleUploadedFile(name, b'not a real pptx', content_type='application/octet-stream')

    def test_missing_file_is_400(self):
        self.client.force_authenticate(user=self.admin)
        assert self.client.post(self.import_url, {}, format='multipart').status_code == 400

    @patch('courses.tasks.import_slideshow_pptx.delay')
    def test_kickoff_sets_pending_and_queues_task(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.import_url, {'file': self._pptx_file()}, format='multipart')
        assert res.status_code == 202
        self.lesson.refresh_from_db()
        assert self.lesson.import_status == Lesson.ImportStatus.PENDING
        assert self.lesson.import_started_at is not None
        mock_delay.assert_called_once()
        assert mock_delay.call_args[0][0] == self.lesson.id

    @patch('courses.tasks.import_slideshow_pptx.delay')
    def test_second_kickoff_while_fresh_pending_is_409(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.import_url, {'file': self._pptx_file()}, format='multipart')
        res = self.client.post(self.import_url, {'file': self._pptx_file()}, format='multipart')
        assert res.status_code == 409
        assert mock_delay.call_count == 1

    @patch('courses.tasks.import_slideshow_pptx.delay')
    def test_kickoff_allowed_once_pending_is_stale(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        self.lesson.import_status = Lesson.ImportStatus.PENDING
        self.lesson.import_started_at = timezone.now() - timedelta(minutes=11)
        self.lesson.save(update_fields=['import_status', 'import_started_at'])
        res = self.client.post(self.import_url, {'file': self._pptx_file()}, format='multipart')
        assert res.status_code == 202
        assert mock_delay.call_count == 1

    def test_non_staff_is_403(self):
        student = User.objects.create_user('student3', 'student3@example.com', 'pw')
        Enrollment.objects.create(user=student, course=self.course, assigned_by=self.admin)
        self.client.force_authenticate(user=student)
        res = self.client.post(self.import_url, {'file': self._pptx_file()}, format='multipart')
        assert res.status_code == 403

    def test_rejects_non_slideshow_lesson(self):
        text_lesson = Lesson.objects.create(
            course=self.course, slug='text2', title='Text2', lesson_type=Lesson.Type.TEXT
        )
        self.client.force_authenticate(user=self.admin)
        url = f'/api/courses/{self.course.id}/lessons/{text_lesson.slug}/import-pptx/'
        res = self.client.post(url, {'file': self._pptx_file()}, format='multipart')
        assert res.status_code == 404


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class SlideshowCompletionGateTest(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('admin4', 'admin4@example.com', 'pw', is_staff=True)
        self.student = User.objects.create_user('student4', 'student4@example.com', 'pw')
        self.course = Course.objects.create(id='course4', title='Course4')
        Enrollment.objects.create(user=self.student, course=self.course, assigned_by=self.admin)
        self.lesson = Lesson.objects.create(
            course=self.course, slug='show5', title='Show5', lesson_type=Lesson.Type.SLIDESHOW
        )
        self.complete_url = f'/api/courses/{self.course.id}/lessons/{self.lesson.slug}/complete/'

    def test_blocked_until_required_slides_visited(self):
        s1 = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image('a.png'))
        SlideshowSlide.objects.create(lesson=self.lesson, order=1, image=make_image('b.png'))
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.complete_url)
        assert res.status_code == 400
        assert 'required slide' in res.data['detail'][0]

        SlideshowSlideVisit.objects.create(user=self.student, slide=s1)
        res2 = self.client.post(self.complete_url)
        assert res2.status_code == 400

    def test_completes_once_all_required_visited(self):
        s1 = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image('a.png'))
        s2 = SlideshowSlide.objects.create(lesson=self.lesson, order=1, image=make_image('b.png'))
        SlideshowSlideVisit.objects.create(user=self.student, slide=s1)
        SlideshowSlideVisit.objects.create(user=self.student, slide=s2)
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.complete_url)
        assert res.status_code == 201, res.data

    def test_optional_slide_excluded_from_gate(self):
        s1 = SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image('a.png'))
        SlideshowSlide.objects.create(
            lesson=self.lesson, order=1, image=make_image('b.png'), is_required=False
        )
        SlideshowSlideVisit.objects.create(user=self.student, slide=s1)
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.complete_url)
        assert res.status_code == 201, res.data

    def test_no_slides_no_blocker(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.complete_url)
        assert res.status_code == 201, res.data

    def test_non_slideshow_lesson_unaffected(self):
        text_lesson = Lesson.objects.create(
            course=self.course, slug='text3', title='Text3', lesson_type=Lesson.Type.TEXT
        )
        self.client.force_authenticate(user=self.student)
        res = self.client.post(
            f'/api/courses/{self.course.id}/lessons/{text_lesson.slug}/complete/'
        )
        assert res.status_code == 201, res.data


class ImportSlideshowPptxTaskTest(TestCase):
    """Calls the task function directly (not via .delay()) — the standard
    way to unit-test Celery task logic without needing a live broker."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix='slideshow-task-media-')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._settings_override = override_settings(MEDIA_ROOT=self._media_root)
        self._settings_override.enable()
        self.addCleanup(self._settings_override.disable)
        self.course = Course.objects.create(id='task-course', title='TaskCourse')
        self.lesson = Lesson.objects.create(
            course=self.course, slug='task-show', title='TaskShow',
            lesson_type=Lesson.Type.SLIDESHOW,
        )

    def _save_tmp_pptx(self, content=b'placeholder'):
        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile
        return default_storage.save('tmp_imports/test.pptx', ContentFile(content))

    def test_lesson_deleted_before_task_starts(self):
        lesson_id = self.lesson.id
        tmp_path = self._save_tmp_pptx()
        self.lesson.delete()
        import_slideshow_pptx(lesson_id, tmp_path)
        from django.core.files.storage import default_storage
        assert not default_storage.exists(tmp_path)

    @patch('courses.tasks.subprocess.run', side_effect=Exception('soffice exploded'))
    def test_conversion_failure_sets_failed_with_sanitized_message(self, mock_run):
        tmp_path = self._save_tmp_pptx()
        import_slideshow_pptx(self.lesson.id, tmp_path)
        self.lesson.refresh_from_db()
        assert self.lesson.import_status == Lesson.ImportStatus.FAILED
        assert 'soffice exploded' not in self.lesson.import_error
        assert 'valid, non-corrupted .pptx' in self.lesson.import_error
        from django.core.files.storage import default_storage
        assert not default_storage.exists(tmp_path)

    def test_lesson_deleted_mid_conversion_aborts_cleanly(self):
        tmp_path = self._save_tmp_pptx()
        lesson_id = self.lesson.id

        fake_page = MagicMock()
        def fake_save(buf, format):
            buf.write(PNG_BYTES)

        fake_page.save.side_effect = fake_save

        def delete_lesson_and_return_pages(*args, **kwargs):
            Lesson.objects.filter(pk=lesson_id).delete()
            return [fake_page]

        fake_prs = MagicMock()
        fake_prs.slide_width = 100
        fake_prs.slide_height = 100
        fake_prs.slides = []

        with patch('courses.tasks.subprocess.run'), \
             patch('courses.tasks.convert_from_path', side_effect=delete_lesson_and_return_pages), \
             patch('courses.tasks.Presentation', return_value=fake_prs):
            import_slideshow_pptx(lesson_id, tmp_path)

        assert SlideshowSlide.objects.filter(lesson_id=lesson_id).count() == 0
        from django.core.files.storage import default_storage
        assert not default_storage.exists(tmp_path)

    def test_acks_late_is_enabled(self):
        assert import_slideshow_pptx.acks_late is True
        assert import_slideshow_pptx.max_retries == 0

    @patch('courses.tasks.subprocess.run')
    @patch('courses.tasks.convert_from_path')
    @patch('courses.tasks.Presentation')
    def test_successful_import_creates_slides_and_resolves_hotspot_targets(
        self, mock_presentation, mock_convert, mock_run,
    ):
        tmp_path = self._save_tmp_pptx()

        pages = []
        for _ in range(2):
            page = MagicMock()
            page.save.side_effect = lambda buf, format: buf.write(PNG_BYTES)
            pages.append(page)
        mock_convert.return_value = pages

        slide0 = MagicMock()
        slide1 = MagicMock()

        good_shape = MagicMock()
        good_shape.left, good_shape.top, good_shape.width, good_shape.height = 10, 10, 20, 20
        good_shape.click_action.target_slide = slide1

        broken_shape = _BrokenClickActionShape()

        no_action_shape = MagicMock()
        no_action_shape.click_action.target_slide = None

        slide0.shapes = [good_shape, broken_shape, no_action_shape]
        slide1.shapes = []

        prs = MagicMock()
        prs.slide_width = 100
        prs.slide_height = 100
        prs.slides = [slide0, slide1]
        mock_presentation.return_value = prs

        import_slideshow_pptx(self.lesson.id, tmp_path)

        self.lesson.refresh_from_db()
        assert self.lesson.import_status == Lesson.ImportStatus.DONE
        assert self.lesson.import_error == ''
        slides = list(self.lesson.slideshow_slides.order_by('order'))
        assert len(slides) == 2
        assert slides[0].hotspots == [
            {'x': 0.1, 'y': 0.1, 'w': 0.2, 'h': 0.2, 'target': slides[1].id}
        ]
        assert slides[1].hotspots == []
        from django.core.files.storage import default_storage
        assert not default_storage.exists(tmp_path)

    @patch('courses.tasks.subprocess.run')
    @patch('courses.tasks.convert_from_path')
    @patch('courses.tasks.Presentation')
    def test_reimport_wholesale_replaces_existing_slides(
        self, mock_presentation, mock_convert, mock_run,
    ):
        SlideshowSlide.objects.create(lesson=self.lesson, order=0, image=make_image())
        tmp_path = self._save_tmp_pptx()

        page = MagicMock()
        page.save.side_effect = lambda buf, format: buf.write(PNG_BYTES)
        mock_convert.return_value = [page]

        slide0 = MagicMock()
        slide0.shapes = []
        prs = MagicMock()
        prs.slide_width = 100
        prs.slide_height = 100
        prs.slides = [slide0]
        mock_presentation.return_value = prs

        import_slideshow_pptx(self.lesson.id, tmp_path)

        self.lesson.refresh_from_db()
        assert self.lesson.slideshow_slides.count() == 1
        assert self.lesson.import_status == Lesson.ImportStatus.DONE
