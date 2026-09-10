import random

from django.db import transaction
from rest_framework import serializers

from .models import (
    Course,
    Enrollment,
    Lesson,
    LessonCompletion,
    Question,
    QuestionOption,
    SlideshowSlide,
    VideoProgress,
)


class QuestionSerializer(serializers.Serializer):
    """Flattens the normalized Question/QuestionOption tables into the shape
    the frontend quiz editor uses: {prompt, options: [str], answer: index}.

    Storage marks the correct option with QuestionOption.is_correct; on the
    wire it is exposed as `answer`, the index of that option.
    """

    prompt = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    answer = serializers.IntegerField(min_value=0)

    def to_representation(self, question):
        options = list(question.options.all())
        correct = next((i for i, o in enumerate(options) if o.is_correct), 0)
        return {
            'prompt': question.prompt,
            'options': [o.text for o in options],
            'answer': correct,
        }

    def validate(self, attrs):
        if attrs['answer'] >= len(attrs['options']):
            raise serializers.ValidationError(
                {'answer': 'answer index is out of range for the options list.'}
            )
        return attrs


class StudentQuestionSerializer(serializers.Serializer):
    """The student-facing view of a question: options are shuffled and the
    correct one is never sent — grading happens server-side (see
    LessonCompletionView) against the canonical, unshuffled data.

    Both the question and option order are re-randomized on every read, so a
    student who answers wrong and re-fetches the lesson sees a new order
    rather than the same one, without needing to persist any attempt state.
    """

    def to_representation(self, question):
        options = list(question.options.all())
        random.shuffle(options)
        return {
            'id': question.id,
            'prompt': question.prompt,
            'options': [{'id': o.id, 'text': o.text} for o in options],
        }


class SlideshowSlideSerializer(serializers.ModelSerializer):
    """Wire shape for one slide of a slideshow lesson. `id` is writable (not
    read-only) even though slides are never created through this serializer
    — LessonSerializer._write_slides needs it in validated_data to match an
    incoming slide entry against an existing row. Slide creation/image
    upload go through their own endpoints (SlideshowSlideCreateView)."""

    id = serializers.IntegerField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = SlideshowSlide
        fields = ['id', 'order', 'image', 'hotspots', 'is_required']

    def get_image(self, obj):
        request = self.context.get('request')
        if not obj.image:
            return None
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url


class LessonSerializer(serializers.ModelSerializer):
    # `id` is the per-course slug; `type` maps onto the model's lesson_type.
    id = serializers.SlugField(source='slug', required=False)
    type = serializers.ChoiceField(
        source='lesson_type', choices=Lesson.Type.choices, default=Lesson.Type.TEXT
    )
    questions = QuestionSerializer(many=True, required=False)
    slides = SlideshowSlideSerializer(
        source='slideshow_slides', many=True, required=False
    )

    class Meta:
        model = Lesson
        fields = [
            'id', 'title', 'type', 'order', 'duration', 'overview',
            'completed', 'html', 'body', 'objectives', 'pro_tip',
            'question_count', 'meta', 'questions',
            'slides', 'import_status', 'import_error',
        ]
        read_only_fields = [
            'order', 'question_count', 'import_status', 'import_error',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        # Staff use this same endpoint to author quizzes in the editor, so
        # they still need the real answer key. Everyone else gets the
        # shuffled, answer-free StudentQuestionSerializer view instead.
        if instance.lesson_type == Lesson.Type.QUIZ and not (user and user.is_staff):
            questions = list(instance.questions.prefetch_related('options').all())
            random.shuffle(questions)
            data['questions'] = StudentQuestionSerializer(questions, many=True).data
        return data

    def _write_questions(self, lesson, questions):
        """Rebuild the lesson's questions/options wholesale from the payload."""
        lesson.questions.all().delete()
        for q_index, q in enumerate(questions):
            question = Question.objects.create(
                lesson=lesson, order=q_index, prompt=q['prompt']
            )
            QuestionOption.objects.bulk_create(
                QuestionOption(
                    question=question,
                    order=o_index,
                    text=text,
                    is_correct=(o_index == q['answer']),
                )
                for o_index, text in enumerate(q['options'])
            )
        lesson.question_count = len(questions)
        lesson.save(update_fields=['question_count'])

    def _write_slides(self, lesson, slides):
        existing = {s.id: s for s in lesson.slideshow_slides.all()}
        valid_targets = set(existing.keys())
        for s in slides:
            obj = existing.get(s.get('id'))
            if obj is None:
                raise serializers.ValidationError(
                    {'slides': f"slide id {s.get('id')} does not belong to this lesson."}
                )
            hotspots = s.get('hotspots', obj.hotspots)
            for h in hotspots:
                if h.get('target') not in valid_targets:
                    raise serializers.ValidationError(
                        {'slides': f"hotspot target {h.get('target')} is not a slide in this lesson."}
                    )
                for key in ('x', 'y', 'w', 'h'):
                    value = h.get(key)
                    if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                        raise serializers.ValidationError(
                            {'slides': f"hotspot {key} must be a number between 0 and 1."}
                        )
            obj.order = s.get('order', obj.order)
            obj.hotspots = hotspots
            obj.is_required = s.get('is_required', obj.is_required)
            obj.save(update_fields=['order', 'hotspots', 'is_required'])

    @transaction.atomic
    def create(self, validated_data):
        questions = validated_data.pop('questions', None)
        # A brand-new lesson has no slide rows yet to match by id — slides
        # always arrive later via the slide-upload endpoint or pptx import.
        validated_data.pop('slideshow_slides', None)
        lesson = Lesson.objects.create(**validated_data)
        if lesson.lesson_type == Lesson.Type.QUIZ and questions is not None:
            self._write_questions(lesson, questions)
        return lesson

    @transaction.atomic
    def update(self, instance, validated_data):
        questions = validated_data.pop('questions', None)
        slides = validated_data.pop('slideshow_slides', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if questions is not None:
            self._write_questions(instance, questions)
        if slides is not None and instance.lesson_type == Lesson.Type.SLIDESHOW:
            self._write_slides(instance, slides)
        return instance


class LessonSummarySerializer(serializers.ModelSerializer):
    """Lightweight lesson representation for embedding in a course list."""

    id = serializers.SlugField(source='slug')
    type = serializers.CharField(source='lesson_type')
    slide_count = serializers.IntegerField(source='slideshow_slides.count', read_only=True)

    class Meta:
        model = Lesson
        fields = [
            'id', 'title', 'type', 'order', 'duration', 'question_count',
            'slide_count',
        ]


class CourseSerializer(serializers.ModelSerializer):
    # Client may supply the slug id on create; the viewset fills it in from
    # the title when omitted. It is immutable once the course exists.
    id = serializers.SlugField(required=False)
    lessons = LessonSummarySerializer(many=True, read_only=True)
    lesson_count = serializers.IntegerField(source='lessons.count', read_only=True)

    class Meta:
        model = Course
        fields = [
            'id', 'title', 'description', 'is_custom',
            'lesson_count', 'lessons', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_fields(self):
        fields = super().get_fields()
        # The slug id can be set on create but never changed on update.
        if self.instance is not None:
            fields['id'].read_only = True
        return fields


class LessonCompletionSerializer(serializers.ModelSerializer):
    course = serializers.CharField(source='lesson.course_id', read_only=True)
    lesson = serializers.CharField(source='lesson.slug', read_only=True)

    class Meta:
        model = LessonCompletion
        fields = ['course', 'lesson', 'completed_at']


class VideoProgressSerializer(serializers.ModelSerializer):
    """One embed's progress for the current user. `provider`/`external_id`
    identify which embed within the lesson this row is for (a lesson can
    have several); `duration_seconds` is read off the shared LessonVideo."""

    provider = serializers.CharField(source='video.provider', read_only=True)
    external_id = serializers.CharField(source='video.external_id', read_only=True)
    duration_seconds = serializers.FloatField(
        source='video.duration_seconds', read_only=True
    )

    class Meta:
        model = VideoProgress
        fields = [
            'provider', 'external_id', 'duration_seconds',
            'max_watched_seconds', 'focused_time_seconds', 'updated_at',
        ]


class EnrollmentSerializer(serializers.ModelSerializer):
    """One course assigned to a student, with its audit trail."""

    course = serializers.CharField(source='course_id', read_only=True)
    title = serializers.CharField(source='course.title', read_only=True)
    assigned_by = serializers.CharField(
        source='assigned_by.username', read_only=True, default=None
    )

    class Meta:
        model = Enrollment
        fields = ['course', 'title', 'assigned_at', 'assigned_by']


class CourseAssignmentSerializer(serializers.Serializer):
    """The full set of courses a student should be enrolled in, as slug ids:
    {"courses": ["design-systems-101", …]}. Anything missing from the list is
    unassigned, so one request describes the end state."""

    courses = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(queryset=Course.objects.all()),
        allow_empty=True,
    )

    def validate_courses(self, value):
        # Ticking the same course twice is harmless, not an error.
        return list(dict.fromkeys(course.pk for course in value))
