from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import RegisterSerializer, UserSerializer


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {'refresh': str(refresh), 'access': str(refresh.access_token)}


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
