from django.contrib import admin

from .models import (
    Course,
    Enrollment,
    Lesson,
    LessonCompletion,
    LessonVideo,
    Question,
    QuestionOption,
    VideoProgress,
)


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0
    fields = ('order', 'title', 'slug', 'lesson_type', 'duration')
    ordering = ('order',)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'is_custom', 'updated_at')
    search_fields = ('id', 'title', 'description')
    inlines = [LessonInline]


class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 0
    fields = ('order', 'text', 'is_correct')
    ordering = ('order',)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'lesson_type', 'order')
    list_filter = ('lesson_type', 'course')
    search_fields = ('title', 'slug')
    ordering = ('course', 'order')


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('prompt', 'lesson', 'order')
    inlines = [QuestionOptionInline]


@admin.register(LessonCompletion)
class LessonCompletionAdmin(admin.ModelAdmin):
    list_display = ('user', 'lesson', 'completed_at')
    list_filter = ('user',)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'assigned_at', 'assigned_by')
    list_filter = ('course',)
    search_fields = ('user__username', 'course__id', 'course__title')


@admin.register(LessonVideo)
class LessonVideoAdmin(admin.ModelAdmin):
    list_display = ('lesson', 'provider', 'external_id', 'duration_seconds')
    list_filter = ('provider',)
    search_fields = ('lesson__title', 'external_id')


@admin.register(VideoProgress)
class VideoProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'video', 'max_watched_seconds', 'focused_time_seconds', 'updated_at')
    list_filter = ('user',)
