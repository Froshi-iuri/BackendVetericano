from django.db import models

class AdopcionAnimal(models.Model):
    nombre = models.CharField(max_length=100)
    raza = models.CharField(max_length=100)
    descripcion = models.TextField()
    # URL pública de la imagen (por ejemplo, Cloudinary). Antes era un
    # ImageField que guardaba el archivo en el contenedor (efímero) y la
    # URL devuelta quedaba rota (404).
    imagen = models.URLField(max_length=500, blank=True, null=True)
    disponible = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nombre} - {self.raza}"
    
class SolicitudAdopcion(models.Model):
    animal = models.ForeignKey(AdopcionAnimal, on_delete=models.CASCADE, related_name='solicitudes')
    nombre_adoptante = models.CharField(max_length=150)
    cedula = models.CharField(max_length=20)
    correo_adoptante = models.EmailField()
    telefono = models.CharField(max_length=20)
    mensaje = models.TextField(blank=True, null=True)
    fecha_solicitud = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Solicitud de {self.nombre_adoptante} para {self.animal.nombre}"


    