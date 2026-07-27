from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Course, Lesson, LessonCompletion
from .serializers import (
    CourseSerializer,
    LessonCompletionSerializer,
    LessonSerializer,
)
from .slugs import unique_slug


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
        completion, created = LessonCompletion.objects.get_or_create(
            user=request.user, lesson=lesson
        )
        return Response(
            LessonCompletionSerializer(completion).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, course_pk, slug):
        lesson = self.get_lesson(course_pk, slug)
        LessonCompletion.objects.filter(user=request.user, lesson=lesson).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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
