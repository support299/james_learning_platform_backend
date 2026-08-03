from django.conf import settings
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


class Enrollment(models.Model):
    """Grants one user access to one course. Staff assign these from the admin
    area; a non-staff user only sees the courses they're enrolled in."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='enrollments',
        on_delete=models.CASCADE,
    )
    course = models.ForeignKey(
        Course, related_name='enrollments', on_delete=models.CASCADE
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    # Who granted it. SET_NULL so removing an admin account doesn't take the
    # students' enrollments with it.
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='assigned_enrollments',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ('user', 'course')
        ordering = ['-assigned_at']

    def __str__(self):
        return f'{self.user} → {self.course_id}'


class LessonCompletion(models.Model):
    """Records that a specific user has completed a specific lesson.
    Completion is per-user, so one row exists per (user, lesson) pair."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='completions',
        on_delete=models.CASCADE,
    )
    lesson = models.ForeignKey(
        Lesson, related_name='completions', on_delete=models.CASCADE
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'lesson')

    def __str__(self):
        return f'{self.user} ✓ {self.lesson_id}'


class LessonVideo(models.Model):
    """One distinct video embed found inside a lesson's `html`. A lesson can
    embed several videos (mixed with text), so each is identified by
    `provider` + `external_id` (parsed from the embed's iframe src, see
    `embeds.py`) rather than by position — reordering or editing the
    surrounding text doesn't break tracking for an already-watched video.

    Lazily get_or_create'd the first time any heartbeat arrives for that
    embed; `duration_seconds` comes from the player and is never overwritten
    once set, so it can't be shrunk later to game the watch-ratio checks."""

    class Provider(models.TextChoices):
        YOUTUBE = 'youtube', 'YouTube'
        LOOM = 'loom', 'Loom'
        VIMEO = 'vimeo', 'Vimeo'

    lesson = models.ForeignKey(
        Lesson, related_name='videos', on_delete=models.CASCADE
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    external_id = models.CharField(max_length=64)
    duration_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ('lesson', 'provider', 'external_id')

    def __str__(self):
        return f'{self.lesson_id} / {self.provider}:{self.external_id}'


class VideoProgress(models.Model):
    """Tracks how much of one lesson embed a user has actually watched, so
    the completion button can be gated on real playback rather than a click.

    Two independent signals are kept: `max_watched_seconds` is the furthest
    content position reached (drives the anti-skip check), and
    `focused_time_seconds` is real wall-clock time spent tab-focused on the
    lesson (catches watching at faster-than-1x playback speed, since content
    position can then outrun real time)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='video_progress',
        on_delete=models.CASCADE,
    )
    video = models.ForeignKey(
        LessonVideo, related_name='progress', on_delete=models.CASCADE
    )
    max_watched_seconds = models.FloatField(default=0.0)
    focused_time_seconds = models.FloatField(default=0.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'video')

    def __str__(self):
        return f'{self.user} ⏵ {self.video_id}'
