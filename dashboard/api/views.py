from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from users.models import Usuarios
from .serializers import DashboardSerializer


class DashboardView(generics.RetrieveAPIView):

    permission_classes = [IsAuthenticated]
    serializer_class = DashboardSerializer

    def get_object(self):
        return self.request.user