import shutil
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework.test import APITestCase

from ..models import Course, Lesson
from ..serializers import LessonSerializer
from ..tasks import import_course_lessons_pptx
from .test_slideshow import PNG_BYTES, make_image

User = get_user_model()

_MEDIA_ROOT = tempfile.mkdtemp(prefix='image-lesson-test-media-')


class _Shapes(list):
    """python-pptx's `slide.shapes` is both iterable and exposes `.title` —
    a plain list can't carry the extra attribute, so subclass it."""

    title = None


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class WriteHotspotsSerializerTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.course = Course.objects.create(id='hc1', title='HC1')
        self.a = Lesson.objects.create(
            course=self.course, slug='a', title='A', lesson_type=Lesson.Type.IMAGE
        )
        self.b = Lesson.objects.create(
            course=self.course, slug='b', title='B', lesson_type=Lesson.Type.IMAGE
        )
        self.other_course_lesson = Lesson.objects.create(
            course=Course.objects.create(id='hc2', title='HC2'),
            slug='a', title='A2', lesson_type=Lesson.Type.IMAGE,
        )

    def _update(self, hotspots):
        serializer = LessonSerializer(
            self.a, data={'hotspots': hotspots}, partial=True, context={}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

    def test_valid_target_saved(self):
        self._update([{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': self.b.slug}])
        self.a.refresh_from_db()
        assert self.a.hotspots == [{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': 'b'}]

    def test_target_outside_course_raises(self):
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': 'not-a-lesson'}])

    def test_same_slug_in_other_course_is_not_a_valid_target(self):
        # self.other_course_lesson also has slug 'a', but lives in a
        # different course — must not be reachable from self.a's hotspots.
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': self.other_course_lesson.slug}])

    def test_self_target_raises(self):
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': self.a.slug}])

    def test_coordinate_out_of_range_raises(self):
        with self.assertRaises(drf_serializers.ValidationError):
            self._update([{'x': 1.5, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': self.b.slug}])

    def test_create_discards_hotspots_payload(self):
        serializer = LessonSerializer(
            data={'title': 'C', 'type': 'image', 'hotspots': [{'x': 0, 'y': 0, 'w': 0, 'h': 0, 'target': 'nope'}]},
            context={},
        )
        serializer.is_valid(raise_exception=True)
        obj = serializer.save(course=self.course, slug='fresh-image')
        assert obj.hotspots == []


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ImportCoursePptxViewTest(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('cadmin', 'cadmin@example.com', 'pw', is_staff=True)
        self.course = Course.objects.create(id='import-course', title='ImportCourse')
        self.import_url = f'/api/courses/{self.course.id}/import-pptx/'

    def _pptx_file(self, name='x.pptx'):
        return SimpleUploadedFile(name, b'not a real pptx', content_type='application/octet-stream')

    def test_missing_file_is_400(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.import_url, {'mode': 'lesson'}, format='multipart')
        assert res.status_code == 400

    def test_invalid_mode_is_400(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            self.import_url, {'mode': 'bogus', 'file': self._pptx_file()}, format='multipart'
        )
        assert res.status_code == 400

    def test_non_staff_is_403(self):
        student = User.objects.create_user('cstudent', 'cstudent@example.com', 'pw')
        self.client.force_authenticate(user=student)
        res = self.client.post(
            self.import_url, {'mode': 'lesson', 'file': self._pptx_file()}, format='multipart'
        )
        assert res.status_code == 403

    @patch('courses.tasks.import_slideshow_pptx.delay')
    def test_slideshow_mode_creates_lesson_and_queues_existing_task(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            self.import_url, {'mode': 'slideshow', 'file': self._pptx_file()}, format='multipart'
        )
        assert res.status_code == 202
        lesson = Lesson.objects.get(course=self.course)
        assert lesson.lesson_type == Lesson.Type.SLIDESHOW
        assert lesson.import_status == Lesson.ImportStatus.PENDING
        mock_delay.assert_called_once_with(lesson.id, mock_delay.call_args[0][1])

    @patch('courses.tasks.import_course_lessons_pptx.delay')
    def test_lesson_mode_sets_course_pending_and_queues_new_task(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            self.import_url, {'mode': 'lesson', 'file': self._pptx_file()}, format='multipart'
        )
        assert res.status_code == 202
        self.course.refresh_from_db()
        assert self.course.import_status == Course.ImportStatus.PENDING
        mock_delay.assert_called_once()
        assert mock_delay.call_args[0][0] == self.course.id

    @patch('courses.tasks.import_course_lessons_pptx.delay')
    def test_lesson_mode_second_kickoff_while_fresh_pending_is_409(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        self.client.post(
            self.import_url, {'mode': 'lesson', 'file': self._pptx_file()}, format='multipart'
        )
        res = self.client.post(
            self.import_url, {'mode': 'lesson', 'file': self._pptx_file()}, format='multipart'
        )
        assert res.status_code == 409
        assert mock_delay.call_count == 1

    @patch('courses.tasks.import_course_lessons_pptx.delay')
    def test_lesson_mode_kickoff_allowed_once_pending_is_stale(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        self.course.import_status = Course.ImportStatus.PENDING
        self.course.import_started_at = timezone.now() - timedelta(minutes=11)
        self.course.save(update_fields=['import_status', 'import_started_at'])
        res = self.client.post(
            self.import_url, {'mode': 'lesson', 'file': self._pptx_file()}, format='multipart'
        )
        assert res.status_code == 202
        assert mock_delay.call_count == 1


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ImportCourseLessonsPptxTaskTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix='image-lesson-task-media-')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._settings_override = override_settings(MEDIA_ROOT=self._media_root)
        self._settings_override.enable()
        self.addCleanup(self._settings_override.disable)
        self.course = Course.objects.create(id='lesson-import-course', title='LessonImportCourse')

    def _save_tmp_pptx(self, content=b'placeholder'):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        return default_storage.save('tmp_imports/test.pptx', ContentFile(content))

    def test_course_deleted_before_task_starts(self):
        course_id = self.course.id
        tmp_path = self._save_tmp_pptx()
        self.course.delete()
        import_course_lessons_pptx(course_id, tmp_path)
        from django.core.files.storage import default_storage
        assert not default_storage.exists(tmp_path)

    @patch('courses.tasks.subprocess.run', side_effect=Exception('soffice exploded'))
    def test_conversion_failure_sets_failed_with_sanitized_message(self, mock_run):
        tmp_path = self._save_tmp_pptx()
        import_course_lessons_pptx(self.course.id, tmp_path)
        self.course.refresh_from_db()
        assert self.course.import_status == Course.ImportStatus.FAILED
        assert 'soffice exploded' not in self.course.import_error
        assert 'valid, non-corrupted .pptx' in self.course.import_error

    @patch('courses.tasks.subprocess.run')
    @patch('courses.tasks.convert_from_path')
    @patch('courses.tasks.Presentation')
    def test_successful_import_creates_one_lesson_per_slide_with_resolved_hotspots(
        self, mock_presentation, mock_convert, mock_run,
    ):
        Lesson.objects.create(
            course=self.course, slug='existing', title='Existing', lesson_type=Lesson.Type.TEXT
        )
        tmp_path = self._save_tmp_pptx()

        pages = []
        for _ in range(2):
            page = MagicMock()
            page.save.side_effect = lambda buf, format: buf.write(PNG_BYTES)
            pages.append(page)
        mock_convert.return_value = pages

        title_frame = MagicMock()
        title_frame.text_frame.text = 'Wrap Up'
        title_frame.has_text_frame = True

        good_shape = MagicMock()
        good_shape.left, good_shape.top, good_shape.width, good_shape.height = 10, 10, 20, 20

        slide0 = MagicMock()
        slide0.shapes = _Shapes([good_shape])
        slide1 = MagicMock()
        slide1.shapes = _Shapes([])
        slide1.shapes.title = title_frame

        good_shape.click_action.target_slide = slide1

        prs = MagicMock()
        prs.slide_width = 100
        prs.slide_height = 100
        prs.slides = [slide0, slide1]
        mock_presentation.return_value = prs

        import_course_lessons_pptx(self.course.id, tmp_path)

        self.course.refresh_from_db()
        assert self.course.import_status == Course.ImportStatus.DONE
        assert self.course.import_error == ''

        image_lessons = list(
            self.course.lessons.filter(lesson_type=Lesson.Type.IMAGE).order_by('order')
        )
        assert len(image_lessons) == 2
        # Appended after the pre-existing text lesson, not colliding with it.
        assert [l.order for l in image_lessons] == [1, 2]
        assert image_lessons[0].hotspots == [
            {'x': 0.1, 'y': 0.1, 'w': 0.2, 'h': 0.2, 'target': image_lessons[1].slug}
        ]
        assert image_lessons[1].hotspots == []
        assert image_lessons[1].title == 'Wrap Up'

        from django.core.files.storage import default_storage
        assert not default_storage.exists(tmp_path)

    @patch('courses.tasks.subprocess.run')
    @patch('courses.tasks.convert_from_path')
    @patch('courses.tasks.Presentation')
    def test_slug_collision_with_existing_lesson_is_disambiguated(
        self, mock_presentation, mock_convert, mock_run,
    ):
        Lesson.objects.create(
            course=self.course, slug='slide-1', title='Slide 1', lesson_type=Lesson.Type.TEXT
        )
        tmp_path = self._save_tmp_pptx()

        page = MagicMock()
        page.save.side_effect = lambda buf, format: buf.write(PNG_BYTES)
        mock_convert.return_value = [page]

        slide0 = MagicMock()
        slide0.shapes = _Shapes([])

        prs = MagicMock()
        prs.slide_width = 100
        prs.slide_height = 100
        prs.slides = [slide0]
        mock_presentation.return_value = prs

        import_course_lessons_pptx(self.course.id, tmp_path)

        new_lesson = self.course.lessons.get(lesson_type=Lesson.Type.IMAGE)
        assert new_lesson.slug != 'slide-1'


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class LessonDeleteHotspotCleanupTest(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('dadmin', 'dadmin@example.com', 'pw', is_staff=True)
        self.course = Course.objects.create(id='del-course', title='DelCourse')
        self.a = Lesson.objects.create(
            course=self.course, slug='a', title='A', lesson_type=Lesson.Type.IMAGE,
            hotspots=[{'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1, 'target': 'b'}],
        )
        self.b = Lesson.objects.create(
            course=self.course, slug='b', title='B', lesson_type=Lesson.Type.IMAGE,
        )

    def test_deleting_target_lesson_strips_dangling_hotspot_from_siblings(self):
        self.client.force_authenticate(user=self.admin)
        url = f'/api/courses/{self.course.id}/lessons/{self.b.slug}/'
        res = self.client.delete(url)
        assert res.status_code == 204
        self.a.refresh_from_db()
        assert self.a.hotspots == []
