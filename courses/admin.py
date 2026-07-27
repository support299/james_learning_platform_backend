from django.contrib import admin

from .models import Course, Lesson, LessonCompletion, Question, QuestionOption


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
