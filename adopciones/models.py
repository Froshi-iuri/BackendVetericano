from django.db import models


class SolicitudAdopcion(models.Model):
    animal = models.ForeignKey('animales.Animal', on_delete=models.CASCADE, related_name='solicitudes_adopcion')
    nombre_adoptante = models.CharField(max_length=150)
    cedula = models.CharField(max_length=20)
    correo_adoptante = models.EmailField()
    telefono = models.CharField(max_length=20)
    mensaje = models.TextField(blank=True, null=True)
    fecha_solicitud = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Solicitud de {self.nombre_adoptante} para {self.animal.nombre}"


    