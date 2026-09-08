from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CourseViewSet,
    ImportSlideshowPptxView,
    LessonCompletionView,
    LessonViewSet,
    MyCompletionsView,
    SlideshowSlideCreateView,
    SlideshowSlideDetailView,
    VideoProgressView,
)

router = DefaultRouter()
router.register('courses', CourseViewSet, basename='course')

# Lessons are nested under a course. Wired by hand to avoid an extra
# nested-router dependency.
lesson_list = LessonViewSet.as_view({'get': 'list', 'post': 'create'})
lesson_detail = LessonViewSet.as_view(
    {'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}
)

urlpatterns = router.urls + [
    path(
        'courses/<slug:course_pk>/lessons/',
        lesson_list,
        name='course-lessons',
    ),
    path(
        'courses/<slug:course_pk>/lessons/<slug:slug>/',
        lesson_detail,
        name='course-lesson-detail',
    ),
    path(
        'courses/<slug:course_pk>/lessons/<slug:slug>/complete/',
        LessonCompletionView.as_view(),
        name='lesson-complete',
    ),
    path(
        'courses/<slug:course_pk>/lessons/<slug:slug>/video-progress/',
        VideoProgressView.as_view(),
        name='video-progress',
    ),
    path(
        'courses/<slug:course_pk>/lessons/<slug:slug>/slides/',
        SlideshowSlideCreateView.as_view(),
        name='lesson-slides',
    ),
    path(
        'courses/<slug:course_pk>/lessons/<slug:slug>/slides/<int:slide_id>/',
        SlideshowSlideDetailView.as_view(),
        name='lesson-slide-detail',
    ),
    path(
        'courses/<slug:course_pk>/lessons/<slug:slug>/import-pptx/',
        ImportSlideshowPptxView.as_view(),
        name='lesson-import-pptx',
    ),
    path('me/completions/', MyCompletionsView.as_view(), name='my-completions'),
]
