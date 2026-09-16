from django.db import models

class EventoVoluntariado(models.Model):
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to='voluntariado_eventos/')
    fecha = models.DateField()

    def __str__(self):
        return self.titulo

class PostulacionVoluntariado(models.Model):
    evento = models.ForeignKey(EventoVoluntariado, on_delete=models.CASCADE, null=True, blank=True)
    correo = models.EmailField()
    identificacion = models.CharField(max_length=50)
    nombre_completo = models.CharField(max_length=150)
    edad = models.IntegerField()
    telefono = models.CharField(max_length=20)
    fecha_postulacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Postulación de {self.nombre_completo}"