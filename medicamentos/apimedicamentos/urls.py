# AnaC
from rest_framework.routers import DefaultRouter
from .views import MedicamentoViewSet

router = DefaultRouter()
router.register(r'', MedicamentoViewSet, basename='medicamento')

urlpatterns = router.urls