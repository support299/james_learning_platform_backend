from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from courses.models import Enrollment
from courses.serializers import (
    CourseAssignmentSerializer,
    EnrollmentSerializer,
)

from .permissions import IsSuperUser
from .serializers import (
    AdminUserSerializer,
    EmailTokenObtainPairSerializer,
    RegisterSerializer,
    StudentSerializer,
    UserSerializer,
)

User = get_user_model()


def tokens_for(user):
    # Goes through the login serializer so tokens minted here carry the same
    # claims (including is_admin) as ones from /login.
    refresh = EmailTokenObtainPairSerializer.get_token(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'is_admin': user.is_staff,
        'is_superadmin': user.is_superuser,
    }


class LoginView(TokenObtainPairView):
    """Exchange an email and password for a JWT pair."""

    serializer_class = EmailTokenObtainPairSerializer


class RegisterView(generics.CreateAPIView):
    """Public signup. Returns the new user plus a JWT pair so the client can
    log in immediately."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {'user': UserSerializer(user).data, **tokens_for(user)},
            status=201,
        )


class MeView(APIView):
    """The currently authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class StudentViewSet(viewsets.ModelViewSet):
    """Staff-only CRUD for student accounts.

    Endpoints:
      GET    /api/auth/students/        list students (?search= by name/email)
      POST   /api/auth/students/        create a student
      GET    /api/auth/students/{id}/   retrieve a student
      PATCH  /api/auth/students/{id}/   update details or reset the password
      DELETE /api/auth/students/{id}/   delete a student
      GET    /api/auth/students/{id}/courses/   courses assigned to them
      PUT    /api/auth/students/{id}/courses/   replace that set of courses
    """

    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        # Students are the non-staff users; admins are managed elsewhere.
        # `ghl_user` is serialized on every row, so pull it in the same query.
        queryset = (
            User.objects.filter(is_staff=False)
            .prefetch_related('ghl_users')
            .select_related('onboarding_agent', 'onboarding_agent__cohort')
            .order_by('-date_joined')
        )
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )
        return queryset

    def enrollments_of(self, student):
        return student.enrollments.select_related('course', 'assigned_by')

    @action(detail=True, methods=['get', 'put'])
    def courses(self, request, pk=None):
        """Read or replace a student's course assignments.

        GET  → [{course, title, assigned_at, assigned_by}, …]
        PUT  {"courses": ["design-systems-101", …]} → the same list, after
        applying it. The payload is the desired end state: courses it omits are
        unassigned, and ones already assigned keep their original audit trail
        rather than being re-stamped.
        """
        student = self.get_object()

        if request.method == 'PUT':
            serializer = CourseAssignmentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            wanted = serializer.validated_data['courses']
            with transaction.atomic():
                student.enrollments.exclude(course_id__in=wanted).delete()
                already = set(
                    student.enrollments.values_list('course_id', flat=True)
                )
                Enrollment.objects.bulk_create(
                    Enrollment(
                        user=student,
                        course_id=course_id,
                        assigned_by=request.user,
                    )
                    for course_id in wanted
                    if course_id not in already
                )

        return Response(
            EnrollmentSerializer(self.enrollments_of(student), many=True).data
        )


class AdminUserViewSet(viewsets.ModelViewSet):
    """Superuser-only management of staff accounts.

    Endpoints:
      GET    /api/auth/admins/               list staff (?search=)
      POST   /api/auth/admins/               create a staff account
      PATCH  /api/auth/admins/{id}/          edit; is_superuser promotes/demotes
      DELETE /api/auth/admins/{id}/          delete
      POST   /api/auth/admins/promote/       {"user_id": n} make an existing
                                             (non-staff) account staff
      POST   /api/auth/admins/{id}/demote/   staff -> plain student account
    """

    serializer_class = AdminUserSerializer
    permission_classes = [IsSuperUser]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = User.objects.filter(is_staff=True).order_by(
            '-is_superuser', 'username'
        )
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )
        return queryset

    def destroy(self, request, *args, **kwargs):
        if self.get_object().pk == request.user.pk:
            return Response(
                {'detail': "You can't delete your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['post'])
    def promote(self, request):
        try:
            target = User.objects.get(pk=request.data.get('user_id'))
        except (User.DoesNotExist, ValueError, TypeError):
            return Response(
                {'detail': 'No such user.'}, status=status.HTTP_404_NOT_FOUND
            )
        if target.is_staff:
            return Response(
                {'detail': 'That account is already staff.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target.is_staff = True
        target.save(update_fields=['is_staff'])
        return Response(self.get_serializer(target).data)

    @action(detail=True, methods=['post'])
    def demote(self, request, pk=None):
        target = self.get_object()
        if target.pk == request.user.pk:
            return Response(
                {'detail': "You can't remove your own admin access."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.is_superuser:
            return Response(
                {'detail': 'Remove superuser status first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target.is_staff = False
        target.save(update_fields=['is_staff'])
        return Response(status=status.HTTP_204_NO_CONTENT)
