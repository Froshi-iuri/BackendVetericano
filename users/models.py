from django.db import models
from django.core.exceptions import ValidationError


# ==========================================
# 1. MODELOS CANÓNICOS DE USUARIOS Y ROLES
# ==========================================

class Rol(models.Model):
    id_rol = models.AutoField(primary_key=True)
    nombre_rol = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'rol'

    def __str__(self):
        return self.nombre_rol


class Usuarios(models.Model):
    id_usuario = models.AutoField(primary_key=True)
    id_rol = models.ForeignKey(Rol, models.DO_NOTHING, db_column='id_rol')
    nombre = models.CharField(max_length=150)
    apellido = models.CharField(max_length=100, blank=True, default='')
    identificacion = models.CharField(unique=True, max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True, max_length=150)
    password = models.CharField(max_length=255)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'usuarios'

    def save(self, *args, **kwargs):
        nombre_rol_actual = self.id_rol.nombre_rol.lower()
        if nombre_rol_actual in ['administrador', 'juridico']:
            existe = Usuarios.objects.filter(id_rol=self.id_rol).exclude(id_usuario=self.id_usuario).exists()
            if existe:
                raise ValidationError(f"Ya existe un usuario registrado con el rol de {self.id_rol.nombre_rol}. Solo se permite uno.")
        super().save(*args, **kwargs)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return f"{self.nombre} ({self.email})"


# ==========================================
# 2. RE-EXPORTACIONES PARA RETROCOMPATIBILIDAD
# ==========================================
# Los modelos fueron organizados en sus apps correspondientes:
# especies, animales, peticiones, patologias, clinica, examenes_clinicos,
# medicamentos, inventario y voluntariado.
# Se re-exportan aquí para garantizar que cualquier importación previa
# (ej. 'from users.models import Animal') siga funcionando sin romperse.

from especies.models import Especie, Raza, Especies  # noqa: F401, E402
from animales.models import Responsables, Animal, EstadosAnimal, AnimalResponsable  # noqa: F401, E402
from peticiones.models import (  # noqa: F401, E402
    TipoPeticion,
    EstadoPeticiones,
    Ubicaciones,
    Peticiones,
    EvidenciaPeticiones,
    SeguimientoPeticionesVisita,
    VisitaAnimal,
)
from patologias.models import Patologia  # noqa: F401, E402
from clinica.models import (  # noqa: F401, E402
    HistoriaClinica,
    Consulta,
    HospitalizacionSeresSintientes,
    Tratamientos,
    Diagnostico,
    SeguimientosClinicos,
    DiagnosticoPatologia,
    SeguimientoHospitalario,
)
from examenes_clinicos.models import Examen, ProcedimientoRealizado  # noqa: F401, E402
from medicamentos.models import (  # noqa: F401, E402
    Medicamento,
    Medicamentos,
    AdministracionMedicamento,
    TratamientoMedicamentos,
)
from inventario.models import (  # noqa: F401, E402
    Proveedores,
    Compra,
    Salidas,
    DetalleCompra,
    DetalleSalida,
    Inventarios,
)
from voluntariado.models import (  # noqa: F401, E402
    EventoVoluntariado,
    PostulacionVoluntariado,
    TiposEventos,
    Eventos,
    VoluntarioEvento,
)