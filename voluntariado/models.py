from django.db import models


# Modelos activos en DRF para el portal de voluntariado
class EventoVoluntariado(models.Model):
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to='voluntariado_eventos/')
    fecha = models.DateField()

    class Meta:
        db_table = 'voluntariado_eventovoluntariado'

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

    class Meta:
        db_table = 'voluntariado_postulacionvoluntariado'

    def __str__(self):
        return f"Postulación de {self.nombre_completo}"


# Modelos del esquema relacional de eventos y voluntariado
class TiposEventos(models.Model):
    id_tipo_evento = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'tipos_eventos'

    def __str__(self):
        return self.nombre


class Eventos(models.Model):
    id_evento = models.AutoField(primary_key=True)
    id_tipo_evento = models.ForeignKey(TiposEventos, models.DO_NOTHING, db_column='id_tipo_evento')
    nombre = models.CharField(max_length=150, blank=True, null=True)
    fecha = models.DateTimeField(blank=True, null=True)
    descripcion = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'eventos'

    def __str__(self):
        return self.nombre or f"Evento #{self.id_evento}"


class VoluntarioEvento(models.Model):
    id_voluntario_evento = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='id_usuario')
    id_evento = models.ForeignKey(Eventos, models.DO_NOTHING, db_column='id_evento')
    id_animal = models.ForeignKey('animales.Animal', models.DO_NOTHING, db_column='id_animal', blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'voluntario_evento'

    def __str__(self):
        return f"Voluntario #{self.id_usuario_id} - Evento #{self.id_evento_id}"