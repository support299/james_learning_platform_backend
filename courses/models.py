from django.db import models


class Course(models.Model):
    """A course in the catalog. The slug `id` mirrors the frontend's IDs
    (e.g. "advanced-ux-research") so links stay stable across the API."""

    id = models.SlugField(primary_key=True, max_length=120)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_custom = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title


class Lesson(models.Model):
    """A lesson is polymorphic by `lesson_type`: video, text (rich HTML), or
    quiz. The `slug` is the API-facing id and is unique per course, matching
    the frontend's per-course uniqueSlug()."""

    class Type(models.TextChoices):
        VIDEO = 'video', 'Video'
        TEXT = 'text', 'Text'
        QUIZ = 'quiz', 'Quiz'

    course = models.ForeignKey(
        Course, related_name='lessons', on_delete=models.CASCADE
    )
    slug = models.SlugField(max_length=120)
    title = models.CharField(max_length=200)
    # Named `lesson_type` to avoid shadowing the built-in `type`; the API
    # exposes it as `type` to match the frontend's lesson.type contract.
    lesson_type = models.CharField(
        max_length=10, choices=Type.choices, default=Type.TEXT
    )
    order = models.PositiveIntegerField(default=0)
    duration = models.CharField(max_length=60, blank=True)
    overview = models.TextField(blank=True)
    completed = models.BooleanField(default=False)

    # `html` is the canonical body: rich text with any number of video embeds
    # inline (YouTube, Loom, Vimeo) as <div data-embed data-provider="…">.
    # Videos live inside this HTML; there is no separate video list.
    html = models.TextField(blank=True)

    # Legacy structured-text fields carried from the seed data.
    body = models.JSONField(default=list, blank=True)
    objectives = models.JSONField(default=list, blank=True)
    pro_tip = models.TextField(blank=True)

    # Quiz metadata (questions live in the Question model).
    question_count = models.PositiveIntegerField(default=0)
    meta = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ['order']
        unique_together = ('course', 'slug')

    def __str__(self):
        return f'{self.course_id} / {self.title}'


class Question(models.Model):
    """A single multiple-choice question belonging to a quiz lesson. The
    correct answer is marked on the option itself (QuestionOption.is_correct),
    so it can't drift when options are reordered or removed."""

    lesson = models.ForeignKey(
        Lesson, related_name='questions', on_delete=models.CASCADE
    )
    order = models.PositiveIntegerField(default=0)
    prompt = models.TextField()

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.prompt[:60]


class QuestionOption(models.Model):
    question = models.ForeignKey(
        Question, related_name='options', on_delete=models.CASCADE
    )
    order = models.PositiveIntegerField(default=0)
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.text
