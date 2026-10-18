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


# ============================================================================
# MODELOS DE INVENTARIO EN DOS NIVELES (FASE 2 - PASO B)
# Esquema real en PostgreSQL / Supabase: plan/supabase_inventario_v2.sql
# Todos los modelos son managed = False con db_table exacto.
# ============================================================================

class VInventarioGeneralLotes(models.Model):
    """
    Vista SQL de solo lectura: v_inventario_general_lotes.
    Resuelve id_medicamento, nombres, compras, proveedores y estados de vencimiento
    del inventario general de bodega CBA sin alterar la tabla base 'inventarios'.
    """
    id_inventario = models.IntegerField(primary_key=True)
    id_medicamento = models.ForeignKey(
        'medicamentos.Medicamentos',
        models.DO_NOTHING,
        db_column='id_medicamento',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='+'
    )
    nombre_medicamento = models.CharField(max_length=150, blank=True, null=True)
    principio_activo = models.CharField(max_length=150, blank=True, null=True)
    presentacion = models.CharField(max_length=100, blank=True, null=True)
    concentracion = models.CharField(max_length=100, blank=True, null=True)
    tipo = models.CharField(max_length=50, blank=True, null=True)
    cantidad_actual = models.IntegerField()
    fecha_vencimiento = models.DateField(blank=True, null=True)
    estado = models.CharField(max_length=50, blank=True, null=True)
    id_compra = models.IntegerField(blank=True, null=True)
    fecha_compra = models.DateTimeField(blank=True, null=True)  # compra.fecha es DateTimeField
    nombre_proveedor = models.CharField(max_length=150, blank=True, null=True)
    estado_vencimiento = models.CharField(max_length=20)  # 'sin_fecha', 'vencido', 'proximo_a_vencer', 'vigente'
    esta_vencido = models.BooleanField()
    disponible_para_asignar = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'v_inventario_general_lotes'

    def __str__(self):
        return f"Lote {self.id_inventario} - {self.nombre_medicamento or 'Sin Fármaco'} ({self.cantidad_actual} uds)"

    def save(self, *args, **kwargs):
        raise NotImplementedError("VInventarioGeneralLotes es una vista SQL de solo lectura. No se permite save().")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("VInventarioGeneralLotes es una vista SQL de solo lectura. No se permite delete().")


class KitVeterinarioLotes(models.Model):
    """
    Tabla: kit_veterinario_lotes.
    Stock individualizado del profesional veterinario por lote de origen.
    Garantiza FIFO nativo mediante fecha_compra_origen e id_inventario.
    Restricción única: uq_kit_vet_lote (id_veterinario, id_inventario) en PostgreSQL.
    """
    id_kit_lote = models.BigAutoField(primary_key=True)
    id_veterinario = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='id_veterinario',
        db_constraint=False,
        related_name='kit_lotes'
    )
    id_inventario = models.ForeignKey(
        Inventarios,
        models.DO_NOTHING,
        db_column='id_inventario',
        db_constraint=False,
        related_name='kit_lotes'
    )
    id_medicamento = models.ForeignKey(
        'medicamentos.Medicamentos',
        models.DO_NOTHING,
        db_column='id_medicamento',
        db_constraint=False,
        related_name='kit_lotes'
    )
    cantidad_actual = models.IntegerField(default=0)      # CHECK (cantidad_actual >= 0)
    cantidad_referencia = models.IntegerField(default=0)  # CHECK (cantidad_referencia >= 0)
    fecha_vencimiento = models.DateField(blank=True, null=True)
    fecha_compra_origen = models.DateTimeField(blank=True, null=True)
    creado_el = models.DateTimeField(auto_now_add=True)
    # NOTA: auto_now se actualiza únicamente en llamadas a model.save(). Consultas
    # directas tipo queryset.update() o bulk_update() NO actualizan este campo
    # automáticamente; debe asignarse explícitamente al actualizar en bloque.
    actualizado_el = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'kit_veterinario_lotes'

    def __str__(self):
        return f"Kit Vet #{self.id_veterinario_id} - Lote #{self.id_inventario_id} (Stock: {self.cantidad_actual})"


class SolicitudesKit(models.Model):
    """
    Tabla: solicitudes_kit.
    Cabecera de solicitudes de reposición o devolución de stock entre veterinarios y gestor.
    """
    id_solicitud = models.BigAutoField(primary_key=True)
    tipo_solicitud = models.CharField(max_length=20)  # CHECK IN ('REPOSICION', 'DEVOLUCION')
    estado = models.CharField(max_length=20, default='PENDIENTE')  # CHECK IN ('PENDIENTE', 'APROBADA', 'PARCIAL', 'RECHAZADA', 'CANCELADA')
    id_veterinario = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='id_veterinario',
        db_constraint=False,
        related_name='solicitudes_kit_solicitadas'
    )
    id_gestor = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='id_gestor',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='solicitudes_kit_gestionadas'
    )
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_resolucion = models.DateTimeField(blank=True, null=True)
    motivo_solicitud = models.TextField()
    observaciones_gestor = models.TextField(blank=True, null=True)
    es_reposicion_total = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = 'solicitudes_kit'

    def __str__(self):
        return f"Solicitud Kit #{self.id_solicitud} ({self.tipo_solicitud} - {self.estado})"


class SolicitudesKitDetalles(models.Model):
    """
    Tabla: solicitudes_kit_detalles.
    Renglones de medicamentos solicitados en una solicitud de reposición o devolución.
    """
    id_detalle_solicitud = models.BigAutoField(primary_key=True)
    id_solicitud = models.ForeignKey(
        SolicitudesKit,
        models.DO_NOTHING,
        db_column='id_solicitud',
        db_constraint=False,
        related_name='detalles'
    )
    id_medicamento = models.ForeignKey(
        'medicamentos.Medicamentos',
        models.DO_NOTHING,
        db_column='id_medicamento',
        db_constraint=False,
        related_name='+'
    )
    id_inventario_origen = models.ForeignKey(
        Inventarios,
        models.DO_NOTHING,
        db_column='id_inventario_origen',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='+'
    )
    cantidad_solicitada = models.IntegerField()  # CHECK (cantidad_solicitada > 0)
    cantidad_aprobada = models.IntegerField(default=0)  # CHECK (cantidad_aprobada >= 0 AND <= cantidad_solicitada)
    es_vencido = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = 'solicitudes_kit_detalles'

    def __str__(self):
        return f"Detalle Solicitud #{self.id_detalle_solicitud} (Med #{self.id_medicamento_id}: {self.cantidad_solicitada})"


class InventarioKardex(models.Model):
    """
    Tabla: inventario_kardex.
    Libro mayor inmutable de movimientos de inventario de farmacia.
    Protegido contra UPDATE/DELETE/TRUNCATE por triggers en PostgreSQL:
    - trg_kardex_inmutable (función fn_proteger_inmutabilidad_kardex())
    - trg_kardex_inmutable_truncate (función fn_proteger_truncate_kardex())
    """
    id_kardex = models.BigAutoField(primary_key=True)
    tipo_movimiento = models.CharField(max_length=30)  # CHECK chk_kardex_tipo
    ubicacion = models.CharField(max_length=20)        # CHECK chk_kardex_ubicacion ('GENERAL', 'KIT_VETERINARIO')
    id_usuario_ejecuta = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='id_usuario_ejecuta',
        db_constraint=False,
        related_name='movimientos_kardex_ejecutados'
    )
    id_veterinario = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='id_veterinario',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='movimientos_kardex_veterinario'
    )
    id_medicamento = models.ForeignKey(
        'medicamentos.Medicamentos',
        models.DO_NOTHING,
        db_column='id_medicamento',
        db_constraint=False,
        related_name='movimientos_kardex'
    )
    id_inventario = models.ForeignKey(
        Inventarios,
        models.DO_NOTHING,
        db_column='id_inventario',
        db_constraint=False,
        related_name='movimientos_kardex'
    )
    cantidad = models.IntegerField()          # CHECK (cantidad > 0)
    saldo_anterior = models.IntegerField()    # CHECK (saldo_anterior >= 0)
    saldo_resultante = models.IntegerField()  # CHECK (saldo_resultante >= 0)
    motivo = models.TextField()
    id_consulta = models.ForeignKey(
        'clinica.Consulta',
        models.DO_NOTHING,
        db_column='id_consulta',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='movimientos_kardex'
    )
    id_tratamiento = models.ForeignKey(
        'clinica.Tratamientos',
        models.DO_NOTHING,
        db_column='id_tratamiento',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='movimientos_kardex'
    )
    id_seguimiento_visita = models.ForeignKey(
        'peticiones.SeguimientoPeticionesVisita',
        models.DO_NOTHING,
        db_column='id_seguimiento_visita',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='movimientos_kardex'
    )
    id_visita_animal = models.ForeignKey(
        'peticiones.VisitaAnimal',
        models.DO_NOTHING,
        db_column='id_visita_animal',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='movimientos_kardex'
    )
    id_solicitud_kit = models.ForeignKey(
        SolicitudesKit,
        models.DO_NOTHING,
        db_column='id_solicitud_kit',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='movimientos_kardex'
    )
    id_kardex_revertido = models.ForeignKey(
        'self',
        models.DO_NOTHING,
        db_column='id_kardex_revertido',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='reversiones'
    )
    client_uuid = models.UUIDField(blank=True, null=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'inventario_kardex'

    def __str__(self):
        return f"Kardex #{self.id_kardex} [{self.tipo_movimiento}] Med #{self.id_medicamento_id} Cant: {self.cantidad}"

    def save(self, *args, **kwargs):
        # Permitir creación inicial (INSERT), pero prohibir modificación (UPDATE).
        # NOTA: Operaciones directas del ORM como queryset.update() o bulk_update()
        # no ejecutan el método save(). La inmutabilidad real y definitiva a nivel de base
        # de datos la garantizan los triggers trg_kardex_inmutable y trg_kardex_inmutable_truncate
        # mediante las funciones fn_proteger_inmutabilidad_kardex() y fn_proteger_truncate_kardex() en PostgreSQL.
        if not self._state.adding:
            raise NotImplementedError(
                "El kardex es inmutable; las correcciones se hacen con un movimiento AJUSTE_REVERSION."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotImplementedError(
            "El kardex es inmutable; no se permite eliminar registros de auditoría farmacéutica."
        )


class InventarioConsumosRegistro(models.Model):
    """
    Tabla: inventario_consumos_registro.
    Auditoría de consumos de insumos clínicos, idempotencia por client_uuid
    y conciliación de conflictos en modo offline.
    """
    id_consumo = models.BigAutoField(primary_key=True)
    client_uuid = models.UUIDField(unique=True)
    id_veterinario = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='id_veterinario',
        db_constraint=False,
        related_name='consumos_registro'
    )
    id_medicamento = models.ForeignKey(
        'medicamentos.Medicamentos',
        models.DO_NOTHING,
        db_column='id_medicamento',
        db_constraint=False,
        related_name='consumos_registro'
    )
    cantidad_solicitada = models.IntegerField()              # CHECK (cantidad_solicitada > 0)
    cantidad_descontada = models.IntegerField(default=0)     # CHECK (cantidad_descontada >= 0)
    estado = models.CharField(max_length=25)                 # CHECK IN ('APLICADO', 'RECHAZADO_CONFLICTO', 'REVERTIDO')
    codigo_http = models.IntegerField(default=200)
    origen_tipo = models.CharField(max_length=30)             # CHECK IN ('HISTORIA_CLINICA', 'ACTA_VISITA', 'DIRECTO')
    id_consulta = models.ForeignKey(
        'clinica.Consulta',
        models.DO_NOTHING,
        db_column='id_consulta',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='consumos_registro'
    )
    id_tratamiento = models.ForeignKey(
        'clinica.Tratamientos',
        models.DO_NOTHING,
        db_column='id_tratamiento',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='consumos_registro'
    )
    id_seguimiento_visita = models.ForeignKey(
        'peticiones.SeguimientoPeticionesVisita',
        models.DO_NOTHING,
        db_column='id_seguimiento_visita',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='consumos_registro'
    )
    id_visita_animal = models.ForeignKey(
        'peticiones.VisitaAnimal',
        models.DO_NOTHING,
        db_column='id_visita_animal',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='consumos_registro'
    )
    motivo_rechazo = models.TextField(blank=True, null=True)
    stock_disponible_al_rechazo = models.IntegerField(blank=True, null=True)
    fecha_consumo = models.DateTimeField(auto_now_add=True)
    fecha_resolucion_conflicto = models.DateTimeField(blank=True, null=True)
    resuelto_por = models.ForeignKey(
        'users.Usuarios',
        models.DO_NOTHING,
        db_column='resuelto_por',
        db_constraint=False,
        blank=True,
        null=True,
        related_name='consumos_resueltos'
    )

    class Meta:
        managed = False
        db_table = 'inventario_consumos_registro'

    def __str__(self):
        return f"Consumo #{self.id_consumo} [{self.estado}] UUID: {self.client_uuid}"

