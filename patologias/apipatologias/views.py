from rest_framework import viewsets
from patologias.models import Patologia
from .serializers import PatologiaSerializer

class PatologiaViewSet(viewsets.ModelViewSet):
    queryset = Patologia.objects.all()
    serializer_class = PatologiaSerializer