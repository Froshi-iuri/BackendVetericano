from django.db import models


class ProcedimientosCatalogo(models.Model):
    id_procedimiento_catalogo = models.AutoField(primary_key=True)
    nombre_tipo = models.CharField(max_length=150)

    class Meta:
        managed = False
        db_table = 'procedimientos_catalogo'

    def __str__(self):
        return self.nombre_tipo


class ProcedimientosRealizados(models.Model):
    id_procedimiento_realizado = models.AutoField(primary_key=True)
    id_consulta = models.ForeignKey('users.Consultas', models.DO_NOTHING, db_column='id_consulta')
    id_procedimiento_catalogo = models.ForeignKey(ProcedimientosCatalogo, models.DO_NOTHING, db_column='id_procedimiento_catalogo')
    resultado_anexo_url = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'procedimientos_realizados'

    def __str__(self):
        return f"Procedimiento {self.id_procedimiento_realizado} - {self.id_procedimiento_catalogo}"

