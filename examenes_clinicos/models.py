from django.db import models  # noqa: F401 (kept for Django app detection)

# Re-export canonical models from users to avoid duplicate table definitions
from users.models import Examen, ProcedimientoRealizado  # noqa: F401

__all__ = ['Examen', 'ProcedimientoRealizado']
