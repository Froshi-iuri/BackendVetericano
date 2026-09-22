from django.db import models


class Proveedores(models.Model):
    id_proveedor = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=150)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    email = models.CharField(max_length=150, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'proveedores'

    def __str__(self):
        return self.nombre


class Compra(models.Model):
    id_compra = models.AutoField(primary_key=True)
    id_proveedor = models.ForeignKey(Proveedores, models.DO_NOTHING, db_column='id_proveedor')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'compra'

    def __str__(self):
        return f"Compra #{self.id_compra}"


class Salidas(models.Model):
    id_salida = models.AutoField(primary_key=True)
    fecha = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'salidas'

    def __str__(self):
        return f"Salida #{self.id_salida}"


class DetalleCompra(models.Model):
    id_detalle_compra = models.AutoField(primary_key=True)
    id_compra = models.ForeignKey(Compra, models.DO_NOTHING, db_column='id_compra')
    id_medicamento = models.ForeignKey('medicamentos.Medicamentos', models.DO_NOTHING, db_column='id_medicamento')
    cantidad = models.IntegerField(blank=True, null=True)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'detalle_compra'

    def __str__(self):
        return f"Detalle Compra #{self.id_detalle_compra}"


class DetalleSalida(models.Model):
    id_detalle_salida = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey('users.Usuarios', models.DO_NOTHING, db_column='id_usuario')
    id_administracion = models.ForeignKey('medicamentos.AdministracionMedicamento', models.DO_NOTHING, db_column='id_administracion')
    cantidad = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'detalle_salida'

    def __str__(self):
        return f"Detalle Salida #{self.id_detalle_salida}"


class Inventarios(models.Model):
    id_inventario = models.AutoField(primary_key=True)
    id_detalle_salida = models.ForeignKey(DetalleSalida, models.DO_NOTHING, db_column='id_detalle_salida', blank=True, null=True)
    id_detalle_compra = models.ForeignKey(DetalleCompra, models.DO_NOTHING, db_column='id_detalle_compra', blank=True, null=True)
    cantidad_actual = models.IntegerField(default=0)
    fecha_vencimiento = models.DateField(blank=True, null=True)
    estado = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'inventarios'

    def __str__(self):
        return f"Inventario #{self.id_inventario} (Stock: {self.cantidad_actual})"
