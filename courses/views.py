from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .embeds import extract_lesson_videos
from .models import (
    Course,
    Lesson,
    LessonCompletion,
    LessonVideo,
    Question,
    VideoProgress,
)
from .serializers import (
    CourseSerializer,
    LessonCompletionSerializer,
    LessonSerializer,
    VideoProgressSerializer,
)
from .slugs import unique_slug

# A student must reach this fraction of the video's duration on both signals
# (content position watched, and wall-clock time spent tab-focused) before
# the lesson can be marked complete.
VIDEO_COMPLETION_THRESHOLD = 0.95

# How far past the previous max_watched_seconds a single heartbeat may jump.
# Whatever a heartbeat reports is stored as-is as long as it's under this —
# this is not a copy of the player's tight in-session anti-skip tolerance,
# it's a coarse server-side backstop against callers that bypass the
# frontend entirely (or send wildly bogus positions), so it's deliberately
# loose: only jumps of ~6 minutes or more get rejected.
MAX_WATCHED_JUMP_TOLERANCE_SECONDS = 360.0

# Per-heartbeat cap on focused_delta_seconds, so a spoofed delta can't
# inflate focused_time_seconds beyond what a normal ~10s heartbeat allows.
MAX_FOCUSED_DELTA_SECONDS = 15.0


class IsStaffOrReadOnly(permissions.BasePermission):
    """Any signed-in user may read the catalog; only staff may change it."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.method in permissions.SAFE_METHODS or request.user.is_staff


def visible_courses(user):
    """The courses `user` is allowed to see: everything for staff, and just
    their assigned courses for a student."""
    queryset = Course.objects.all()
    if not user.is_staff:
        queryset = queryset.filter(enrollments__user=user)
    return queryset


class CourseViewSet(viewsets.ModelViewSet):
    """CRUD for courses, plus a `reorder` action for their lessons.

    Students see only the courses assigned to them; staff see the whole
    catalog and are the only ones who can change it.

    Endpoints:
      GET    /api/courses/                list courses
      POST   /api/courses/                create a course
      GET    /api/courses/{id}/           retrieve a course (with lessons)
      PATCH  /api/courses/{id}/           update course details
      DELETE /api/courses/{id}/           delete a course (cascades lessons)
      POST   /api/courses/{id}/reorder/   reorder lessons by id
    """

    serializer_class = CourseSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        return visible_courses(self.request.user).prefetch_related('lessons')

    def perform_create(self, serializer):
        # Fill in the slug id from the title when the client didn't supply one.
        course_id = serializer.validated_data.get('id')
        if not course_id:
            existing = set(Course.objects.values_list('id', flat=True))
            course_id = unique_slug(serializer.validated_data['title'], existing)
        serializer.save(id=course_id)

    @action(detail=True, methods=['post'])
    def reorder(self, request, pk=None):
        """Set lesson order from a full list of lesson ids, e.g.
        {"lessons": ["intro", "module-quiz", "wrap-up"]}."""
        course = self.get_object()
        ordered_ids = request.data.get('lessons', [])
        current = set(course.lessons.values_list('slug', flat=True))
        if set(ordered_ids) != current:
            return Response(
                {'lessons': 'Must list exactly the ids of this course\'s lessons.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            for position, slug in enumerate(ordered_ids):
                course.lessons.filter(slug=slug).update(order=position)
            course.save()  # bump updated_at
        # Re-fetch so the prefetched lessons reflect the new order, not the
        # stale cache from get_object().
        fresh = self.get_queryset().get(pk=course.pk)
        serializer = self.get_serializer(fresh)
        return Response(serializer.data)


class LessonViewSet(viewsets.ModelViewSet):
    """CRUD for the lessons (and quizzes) nested under a course.

    Endpoints (course_pk = the course id):
      GET    /api/courses/{course_pk}/lessons/           list lessons
      POST   /api/courses/{course_pk}/lessons/           add a lesson or quiz
      GET    /api/courses/{course_pk}/lessons/{id}/       retrieve a lesson
      PATCH  /api/courses/{course_pk}/lessons/{id}/       update a lesson
      DELETE /api/courses/{course_pk}/lessons/{id}/       delete a lesson
    """

    serializer_class = LessonSerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = 'slug'
    lookup_url_kwarg = 'slug'

    def get_course(self):
        # 404 rather than 403 for a course the student isn't assigned: an
        # unassigned course shouldn't be distinguishable from a missing one.
        return get_object_or_404(
            visible_courses(self.request.user), pk=self.kwargs['course_pk']
        )

    def get_queryset(self):
        return Lesson.objects.filter(course=self.get_course())

    def perform_create(self, serializer):
        course = self.get_course()
        existing = set(course.lessons.values_list('slug', flat=True))
        slug = serializer.validated_data.get('slug')
        slug = (
            slug if slug and slug not in existing
            else unique_slug(serializer.validated_data['title'], existing)
        )
        # Append to the end of the course's lesson list.
        next_order = course.lessons.count()
        serializer.save(course=course, slug=slug, order=next_order)
        course.save()  # bump updated_at

    def perform_update(self, serializer):
        serializer.save()
        self.get_course().save()  # bump updated_at

    def perform_destroy(self, instance):
        course = instance.course
        instance.delete()
        course.save()  # bump updated_at


class LessonCompletionView(APIView):
    """Per-user completion toggle for a single lesson.

      POST   /api/courses/{course_pk}/lessons/{slug}/complete/   mark done
      DELETE /api/courses/{course_pk}/lessons/{slug}/complete/   unmark
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_lesson(self, course_pk, slug):
        course = get_object_or_404(
            visible_courses(self.request.user), pk=course_pk
        )
        return get_object_or_404(Lesson, course=course, slug=slug)

    def post(self, request, course_pk, slug):
        lesson = self.get_lesson(course_pk, slug)
        # Keyed off actual embed content, not `lesson_type` — lessons authored
        # through the editor are always saved as `lesson_type: 'text'`
        # regardless of content, so `lesson_type == VIDEO` would never match
        # real data. `_video_completion_blockers` already no-ops ([]) when
        # the lesson's html has no video embeds.
        reasons = self._video_completion_blockers(request.user, lesson)
        reasons += self._quiz_completion_blockers(request, lesson)
        if reasons:
            return Response({'detail': reasons}, status=status.HTTP_400_BAD_REQUEST)
        completion, created = LessonCompletion.objects.get_or_create(
            user=request.user, lesson=lesson
        )
        return Response(
            LessonCompletionSerializer(completion).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @staticmethod
    def _video_completion_blockers(user, lesson):
        """Reasons `lesson` can't be marked complete yet, or [] if eligible.

        Every video embed found in the lesson's html must individually clear
        both thresholds — a lesson can embed more than one video."""
        embeds = extract_lesson_videos(lesson.html)
        if not embeds:
            return []

        progress_by_video = {
            p.video_id: p
            for p in VideoProgress.objects.filter(
                user=user, video__lesson=lesson
            ).select_related('video')
        }
        videos_by_key = {
            (v.provider, v.external_id): v
            for v in LessonVideo.objects.filter(lesson=lesson)
        }

        watched_short = focused_short = False
        for provider, external_id in embeds:
            video = videos_by_key.get((provider, external_id))
            progress = progress_by_video.get(video.id) if video else None
            duration = video.duration_seconds if video else None
            watched = progress.max_watched_seconds if progress else 0.0
            focused = progress.focused_time_seconds if progress else 0.0
            watched_ratio = (watched / duration) if duration else 0.0
            focused_ratio = (focused / duration) if duration else 0.0
            if watched_ratio < VIDEO_COMPLETION_THRESHOLD:
                watched_short = True
            if focused_ratio < VIDEO_COMPLETION_THRESHOLD:
                focused_short = True

        reasons = []
        if watched_short:
            reasons.append(
                'You need to watch at least 95% of every video in this '
                'lesson before marking it complete.'
            )
        if focused_short:
            reasons.append(
                'Spend more time actively watching this lesson before '
                'marking it complete.'
            )
        return reasons

    @staticmethod
    def _quiz_completion_blockers(request, lesson):
        """A quiz lesson can't be marked complete until every question has
        been answered correctly. `request.data['answers']` is expected as
        {question_id: option_id}, matching the shuffled-but-id-stable shape
        StudentQuestionSerializer sends the client — grading is done here
        against the real is_correct flags, never trusting a client-side
        notion of which answer is right."""
        if lesson.lesson_type != Lesson.Type.QUIZ:
            return []
        questions = list(
            Question.objects.filter(lesson=lesson).prefetch_related('options')
        )
        if not questions:
            return []

        answers = request.data.get('answers')
        if not isinstance(answers, dict):
            answers = {}

        for question in questions:
            correct_option = next(
                (o for o in question.options.all() if o.is_correct), None
            )
            selected_id = answers.get(str(question.id))
            if (
                correct_option is None
                or selected_id is None
                or str(selected_id) != str(correct_option.id)
            ):
                return [
                    'Answer every question correctly before marking this '
                    'lesson complete.'
                ]
        return []

    def delete(self, request, course_pk, slug):
        lesson = self.get_lesson(course_pk, slug)
        LessonCompletion.objects.filter(user=request.user, lesson=lesson).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VideoProgressView(APIView):
    """Per-user watch progress for the video embed(s) in a lesson. A lesson's
    html can contain more than one video, so progress is reported per embed,
    identified by `provider` + `external_id` (parsed from the embed's src).

      GET  /api/courses/{course_pk}/lessons/{slug}/video-progress/   progress for every embed
      POST /api/courses/{course_pk}/lessons/{slug}/video-progress/   heartbeat for one embed
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_lesson(self, course_pk, slug):
        course = get_object_or_404(
            visible_courses(self.request.user), pk=course_pk
        )
        return get_object_or_404(Lesson, course=course, slug=slug)

    def get(self, request, course_pk, slug):
        lesson = self.get_lesson(course_pk, slug)
        embeds = extract_lesson_videos(lesson.html)
        progress_rows = []
        for provider, external_id in embeds:
            video, _ = LessonVideo.objects.get_or_create(
                lesson=lesson, provider=provider, external_id=external_id
            )
            progress, _ = VideoProgress.objects.get_or_create(
                user=request.user, video=video
            )
            progress_rows.append(progress)
        return Response(VideoProgressSerializer(progress_rows, many=True).data)

    def post(self, request, course_pk, slug):
        lesson = self.get_lesson(course_pk, slug)
        provider = request.data.get('provider')
        external_id = request.data.get('external_id')
        current_time = request.data.get('current_time')
        focused_delta = request.data.get('focused_delta_seconds', 0)
        duration = request.data.get('duration_seconds')

        if current_time is None or not provider or not external_id:
            return Response(
                {'detail': 'provider, external_id and current_time are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # The embed must actually belong to this lesson's html — reject
        # progress reports for a video id that isn't part of the lesson.
        if (provider, external_id) not in extract_lesson_videos(lesson.html):
            # The video may have been part of the lesson before an edit
            # removed it, in which case earlier progress still exists — tell
            # the client the last saved position so the player can seek back
            # to it instead of losing the viewer's place silently.
            last_position = None
            last_updated = None
            existing_video = LessonVideo.objects.filter(
                lesson=lesson, provider=provider, external_id=external_id
            ).first()
            if existing_video:
                progress = VideoProgress.objects.filter(
                    user=request.user, video=existing_video
                ).first()
                if progress:
                    last_position = progress.max_watched_seconds
                    last_updated = progress.updated_at
            return Response(
                {
                    'detail': 'This video is not part of this lesson.',
                    'last_position_seconds': last_position,
                    'last_updated': last_updated,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_time = float(current_time)
        focused_delta = float(focused_delta or 0)

        video, _ = LessonVideo.objects.get_or_create(
            lesson=lesson, provider=provider, external_id=external_id
        )
        # Set the authoritative duration once, from the player's real value.
        # Never overwritten after that so it can't be shrunk to game the ratio.
        if video.duration_seconds is None and duration:
            video.duration_seconds = float(duration)
            video.save(update_fields=['duration_seconds'])

        progress, _ = VideoProgress.objects.get_or_create(user=request.user, video=video)

        clamped_time = current_time
        if video.duration_seconds:
            clamped_time = min(clamped_time, video.duration_seconds)
        if clamped_time > progress.max_watched_seconds + MAX_WATCHED_JUMP_TOLERANCE_SECONDS:
            return Response(
                {'current_time': 'Reported position jumped too far ahead.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        progress.max_watched_seconds = max(progress.max_watched_seconds, clamped_time)
        progress.focused_time_seconds += max(
            0.0, min(focused_delta, MAX_FOCUSED_DELTA_SECONDS)
        )
        progress.save(update_fields=['max_watched_seconds', 'focused_time_seconds', 'updated_at'])
        return Response(VideoProgressSerializer(progress).data)


class MyCompletionsView(generics.ListAPIView):
    """Every lesson the current user has completed, as {course, lesson,
    completed_at}. The client builds its completion map from this."""

    serializer_class = LessonCompletionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return LessonCompletion.objects.filter(
            user=self.request.user
        ).select_related('lesson')
