"""
TESTS DE CONTRATO DE BASE DE DATOS E INMUTABILIDAD (BLOQUE E: TEST-SCH-50 A TEST-SCH-55).

Cubre:
- TEST-SCH-50: Inmutabilidad de inventario_kardex en capa ORM (save y delete).
- TEST-SCH-51: Inmutabilidad de inventario_kardex ante UPDATE directo en PostgreSQL (trigger trg_kardex_inmutable).
- TEST-SCH-52: Inmutabilidad de inventario_kardex ante DELETE directo en PostgreSQL (trigger trg_kardex_inmutable).
- TEST-SCH-53: Inmutabilidad de inventario_kardex ante TRUNCATE directo en PostgreSQL (trigger trg_kardex_inmutable_truncate).
- TEST-SCH-54: Validación funcional de la vista v_inventario_general_lotes (columnas, estados y disponibilidad).
- TEST-SCH-55: Teardown dinámico, limpieza completa e idempotencia de re-siembra (VetericanoTransactionTestCase).

Especificación de referencia:
- plan/DISENO_TAREA10.md: §4.1, §4.2, §5.5 (Bloque E).
- plan/DECISIONES.md: §4, §5.
"""

from datetime import timedelta
import uuid

from django.db import connection, transaction
from django.db.utils import InternalError
from django.test import TestCase
from django.utils import timezone

from clinica.models import Consulta, Tratamientos
from inventario.models import (
    InventarioConsumosRegistro,
    InventarioKardex,
    Inventarios,
    KitVeterinarioLotes,
    SolicitudesKit,
    SolicitudesKitDetalles,
    VInventarioGeneralLotes,
)
from inventario.tests.base import VetericanoTransactionTestCase
from inventario.tests.factories import (
    crear_animal_con_historia,
    crear_lote_bodega,
    crear_lote_kit,
    crear_medicamento,
    crear_peticion_visita,
    crear_usuario,
)
from peticiones.models import SeguimientoPeticionesVisita, VisitaAnimal


class ContratoEsquemaTestCase(TestCase):
    """
    Casos TEST-SCH-50 a TEST-SCH-54 ejecutados bajo transacciones aisladas con rollback (django.test.TestCase).
    """

    def setUp(self):
        super().setUp()
        self.hoy = timezone.now().date()
        self.usuario = crear_usuario(rol_id=3, email="vet_contrato_sch@cba.gov.co")
        self.medicamento = crear_medicamento(nombre="Amoxicilina 500mg Test")
        self.lote_bodega = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=50,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            fecha_compra=timezone.now() - timedelta(days=10),
            estado="Disponible",
        )
        self.kardex = InventarioKardex.objects.create(
            tipo_movimiento="ENTRADA_COMPRA",
            ubicacion="GENERAL",
            id_usuario_ejecuta=self.usuario,
            id_veterinario=None,
            id_medicamento=self.medicamento,
            id_inventario=self.lote_bodega,
            cantidad=50,
            saldo_anterior=0,
            saldo_resultante=50,
            motivo="Carga inicial en bodega para pruebas de inmutabilidad",
        )

    def test_sch_50_inmutabilidad_kardex_capa_orm(self):
        """
        TEST-SCH-50: Inmutabilidad de inventario_kardex en capa ORM.
        Verifica que kardex.save() sobre fila existente lance NotImplementedError
        con mensaje 'El kardex es inmutable; las correcciones se hacen con un movimiento AJUSTE_REVERSION.'
        (inventario/models.py:382-385) y kardex.delete() lance NotImplementedError
        con mensaje 'El kardex es inmutable; no se permite eliminar registros de auditoría farmacéutica.'
        (inventario/models.py:388-391).
        """
        # 1. Modificación de fila existente mediante save()
        with self.assertRaises(NotImplementedError) as ctx_save:
            self.kardex.cantidad = 99
            self.kardex.save()
        self.assertEqual(
            str(ctx_save.exception),
            "El kardex es inmutable; las correcciones se hacen con un movimiento AJUSTE_REVERSION.",
        )

        # 2. Eliminación mediante delete()
        with self.assertRaises(NotImplementedError) as ctx_delete:
            self.kardex.delete()
        self.assertEqual(
            str(ctx_delete.exception),
            "El kardex es inmutable; no se permite eliminar registros de auditoría farmacéutica.",
        )

    def test_sch_51_inmutabilidad_kardex_update_directo(self):
        """
        TEST-SCH-51: Inmutabilidad de inventario_kardex ante UPDATE directo en PostgreSQL.
        El trigger trg_kardex_inmutable (función fn_proteger_inmutabilidad_kardex)
        debe rechazar la operación elevando excepción con código SQLSTATE 'P0001'.
        Estructura requerida: with self.assertRaises(InternalError): with transaction.atomic():
        """
        with connection.cursor() as cursor:
            with self.assertRaises(InternalError) as ctx:
                with transaction.atomic():
                    cursor.execute(
                        "UPDATE public.inventario_kardex SET cantidad = 99 WHERE id_kardex = %s",
                        [self.kardex.id_kardex],
                    )

            self.assertIsNotNone(ctx.exception.__cause__)
            self.assertEqual(ctx.exception.__cause__.pgcode, "P0001")

            # Comprobar que la conexión sigue sana tras la excepción y rollback del savepoint
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

        # Comprobar que la fila de kardex sigue intacta
        self.kardex.refresh_from_db()
        self.assertEqual(self.kardex.cantidad, 50)

    def test_sch_52_inmutabilidad_kardex_delete_directo(self):
        """
        TEST-SCH-52: Inmutabilidad de inventario_kardex ante DELETE directo en PostgreSQL.
        El trigger trg_kardex_inmutable (función fn_proteger_inmutabilidad_kardex)
        debe rechazar la operación elevando excepción con código SQLSTATE 'P0001'.
        Estructura requerida: with self.assertRaises(InternalError): with transaction.atomic():
        """
        with connection.cursor() as cursor:
            with self.assertRaises(InternalError) as ctx:
                with transaction.atomic():
                    cursor.execute(
                        "DELETE FROM public.inventario_kardex WHERE id_kardex = %s",
                        [self.kardex.id_kardex],
                    )

            self.assertIsNotNone(ctx.exception.__cause__)
            self.assertEqual(ctx.exception.__cause__.pgcode, "P0001")

            # Comprobar que la conexión sigue sana
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

        # Comprobar que la fila de kardex sigue intacta
        self.assertTrue(InventarioKardex.objects.filter(id_kardex=self.kardex.id_kardex).exists())

    def test_sch_53_inmutabilidad_kardex_truncate_directo(self):
        """
        TEST-SCH-53: Inmutabilidad de inventario_kardex ante TRUNCATE directo en PostgreSQL.
        El trigger trg_kardex_inmutable_truncate (función fn_proteger_truncate_kardex)
        debe rechazar la operación elevando excepción con código SQLSTATE 'P0001'
        y el mensaje literal definido en schema_public_clean.sql:129.
        Estructura requerida: with self.assertRaises(InternalError): with transaction.atomic():
        """
        mensaje_literal_esperado = (
            "Operación denegada en tabla inventario_kardex: "
            "El kardex es inmutable. Prohibido ejecutar TRUNCATE."
        )

        with connection.cursor() as cursor:
            with self.assertRaises(InternalError) as ctx:
                with transaction.atomic():
                    cursor.execute("TRUNCATE TABLE public.inventario_kardex")

            self.assertIsNotNone(ctx.exception.__cause__)
            self.assertEqual(ctx.exception.__cause__.pgcode, "P0001")
            self.assertIn(mensaje_literal_esperado, str(ctx.exception))

            # Comprobar que la conexión sigue sana
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

        # Comprobar que la fila de kardex sigue intacta
        self.assertTrue(InventarioKardex.objects.filter(id_kardex=self.kardex.id_kardex).exists())

    def test_sch_54_validacion_funcional_vista_inventario_general_lotes(self):
        """
        TEST-SCH-54: Validación funcional de la vista v_inventario_general_lotes.
        Comprueba las 16 columnas reales de la vista, los valores reales de estado_vencimiento
        ('vencido', 'proximo_a_vencer', 'vigente', 'sin_fecha'), y la disponibilidad calculada
        en lotes vencido, próximo, vigente, sin fecha, agotado y no disponible.
        """
        # Creación de lotes que cubren todos los estados de vencimiento y disponibilidad
        lote_vencido = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=10,
            fecha_vencimiento=self.hoy - timedelta(days=5),
            estado="Disponible",
        )
        lote_proximo = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=20,
            fecha_vencimiento=self.hoy + timedelta(days=15),
            estado="Disponible",
        )
        lote_vigente = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=30,
            fecha_vencimiento=self.hoy + timedelta(days=90),
            estado="Disponible",
        )
        lote_sin_fecha = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=15,
            fecha_vencimiento=None,
            estado="Disponible",
        )
        lote_agotado = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=0,
            fecha_vencimiento=self.hoy + timedelta(days=60),
            estado="Agotado",
        )
        lote_no_disponible = crear_lote_bodega(
            medicamento=self.medicamento,
            cantidad=25,
            fecha_vencimiento=self.hoy + timedelta(days=60),
            estado="Cuarentena",
        )

        # 1. Verificación de las 16 columnas reales de la vista en PostgreSQL
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM public.v_inventario_general_lotes LIMIT 0")
            columnas_reales = [col[0] for col in cursor.description]

        columnas_esperadas = [
            "id_inventario",
            "id_medicamento",
            "nombre_medicamento",
            "principio_activo",
            "presentacion",
            "concentracion",
            "tipo",
            "cantidad_actual",
            "fecha_vencimiento",
            "estado",
            "id_compra",
            "fecha_compra",
            "nombre_proveedor",
            "estado_vencimiento",
            "esta_vencido",
            "disponible_para_asignar",
        ]
        self.assertEqual(columnas_reales, columnas_esperadas)

        # 2. Lote Vencido (fecha_vencimiento < CURRENT_DATE)
        vista_vencido = VInventarioGeneralLotes.objects.get(id_inventario=lote_vencido.id_inventario)
        self.assertEqual(vista_vencido.estado_vencimiento, "vencido")
        self.assertIs(vista_vencido.esta_vencido, True)
        self.assertIs(vista_vencido.disponible_para_asignar, False)

        # 3. Lote Próximo a Vencer (fecha_vencimiento <= CURRENT_DATE + 30 days)
        vista_proximo = VInventarioGeneralLotes.objects.get(id_inventario=lote_proximo.id_inventario)
        self.assertEqual(vista_proximo.estado_vencimiento, "proximo_a_vencer")
        self.assertIs(vista_proximo.esta_vencido, False)
        self.assertIs(vista_proximo.disponible_para_asignar, True)

        # 4. Lote Vigente (fecha_vencimiento > CURRENT_DATE + 30 days)
        vista_vigente = VInventarioGeneralLotes.objects.get(id_inventario=lote_vigente.id_inventario)
        self.assertEqual(vista_vigente.estado_vencimiento, "vigente")
        self.assertIs(vista_vigente.esta_vencido, False)
        self.assertIs(vista_vigente.disponible_para_asignar, True)

        # 5. Lote Sin Fecha (fecha_vencimiento IS NULL)
        vista_sin_fecha = VInventarioGeneralLotes.objects.get(id_inventario=lote_sin_fecha.id_inventario)
        self.assertEqual(vista_sin_fecha.estado_vencimiento, "sin_fecha")
        self.assertIs(vista_sin_fecha.esta_vencido, False)
        self.assertIs(vista_sin_fecha.disponible_para_asignar, True)

        # 6. Lote Agotado (cantidad_actual == 0, estado == 'Agotado')
        vista_agotado = VInventarioGeneralLotes.objects.get(id_inventario=lote_agotado.id_inventario)
        self.assertEqual(vista_agotado.estado_vencimiento, "vigente")
        self.assertIs(vista_agotado.esta_vencido, False)
        self.assertIs(vista_agotado.disponible_para_asignar, False)

        # 7. Lote No Disponible por Estado (estado == 'Cuarentena')
        vista_no_disp = VInventarioGeneralLotes.objects.get(id_inventario=lote_no_disponible.id_inventario)
        self.assertEqual(vista_no_disp.estado_vencimiento, "vigente")
        self.assertIs(vista_no_disp.esta_vencido, False)
        self.assertIs(vista_no_disp.disponible_para_asignar, False)


class ContratoEsquemaTeardownTestCase(VetericanoTransactionTestCase):
    """
    Caso TEST-SCH-55 ejecutado bajo VetericanoTransactionTestCase para probar
    el teardown dinámico, inmutabilidad de kardex con session_replication_role,
    limpieza de tablas operacionales y re-siembra de catálogos base con reinicio de secuencias.
    """

    def test_sch_55_teardown_dinamico_limpieza_e_idempotencia_resiembra(self):
        """
        TEST-SCH-55: Limpieza completa e idempotencia de re-siembra en teardown.
        1. Crea datos en todas las tablas de negocio posibles con factories/modelos,
           incluyendo kardex poblado.
        2. Invoca self._fixture_teardown(). Demuestra que TRUNCATE con
           session_replication_role='replica' salta los triggers de inmutabilidad del kardex.
        3. Afirma 0 filas en cada tabla de negocio operacional (verificando nombres reales del esquema).
        4. Afirma que los catálogos base quedaron resembrados:
           rol (4), tipo_peticion (1), estado_peticiones (2), especies (1), raza (1).
        5. Afirma que las secuencias reinician (crear un usuario nuevo tras teardown da id_usuario=1).
        """
        hoy = timezone.now().date()

        # Poblar usuarios
        vet = crear_usuario(rol_id=3, email="vet_teardown_sch@cba.gov.co")
        gestor = crear_usuario(rol_id=1, email="gestor_teardown_sch@cba.gov.co")

        # Poblar catálogo medicamentos y lote de bodega (proveedores, compra, detalle_compra, inventarios)
        med = crear_medicamento(nombre="Meloxicam Teardown Test")
        lote_bodega = crear_lote_bodega(
            medicamento=med,
            cantidad=40,
            fecha_vencimiento=hoy + timedelta(days=60),
            estado="Disponible",
        )

        # Poblar kit_veterinario_lotes
        lote_kit = crear_lote_kit(
            veterinario=vet,
            lote_bodega=lote_bodega,
            cantidad=10,
            cantidad_ref=10,
        )

        # Poblar solicitudes_kit y solicitudes_kit_detalles
        solicitud = SolicitudesKit.objects.create(
            tipo_solicitud="REPOSICION",
            estado="PENDIENTE",
            id_veterinario=vet,
            motivo_solicitud="Solicitud para prueba de teardown dinámico",
            es_reposicion_total=False,
        )
        solicitud_det = SolicitudesKitDetalles.objects.create(
            id_solicitud=solicitud,
            id_medicamento=med,
            cantidad_solicitada=5,
            cantidad_aprobada=0,
            es_vencido=False,
        )

        # Poblar animal e historia_clinica
        animal, historia = crear_animal_con_historia()

        # Poblar consulta y tratamientos
        consulta = Consulta.objects.create(
            id_historia=historia,
            motivo_consulta="Consulta para teardown",
        )
        tratamiento = Tratamientos.objects.create(
            id_consulta=consulta,
            descripcion="Tratamiento para teardown",
        )

        # Poblar peticiones, seguimiento_peticiones_visita y visita_animal
        peticion = crear_peticion_visita(veterinario=vet)
        seguimiento_visita = SeguimientoPeticionesVisita.objects.create(
            id_peticion=peticion,
            id_veterinario=vet,
            observacion="Seguimiento para teardown",
        )
        visita_animal = VisitaAnimal.objects.create(
            id_animal=animal,
            id_seguimiento=seguimiento_visita,
            nro_radicado_atencion="RAD-TD-99",
        )

        # Poblar inventario_consumos_registro
        client_uuid = uuid.uuid4()
        consumo_reg = InventarioConsumosRegistro.objects.create(
            client_uuid=client_uuid,
            id_veterinario=vet,
            id_medicamento=med,
            cantidad_solicitada=2,
            cantidad_descontada=2,
            estado="APLICADO",
            codigo_http=200,
            origen_tipo="HISTORIA_CLINICA",
            id_consulta=consulta,
        )

        # Poblar inventario_kardex (clave para probar bypass de trigger con replica en TRUNCATE)
        kardex = InventarioKardex.objects.create(
            tipo_movimiento="CONSUMO_HISTORIA",
            ubicacion="KIT_VETERINARIO",
            id_usuario_ejecuta=vet,
            id_veterinario=vet,
            id_medicamento=med,
            id_inventario=lote_bodega,
            cantidad=2,
            saldo_anterior=10,
            saldo_resultante=8,
            motivo="Consumo clínico en test teardown",
            id_consulta=consulta,
            client_uuid=client_uuid,
        )

        # Tablas de negocio operacionales a verificar
        tablas_negocio = [
            "usuarios",
            "inventarios",
            "kit_veterinario_lotes",
            "solicitudes_kit",
            "solicitudes_kit_detalles",
            "inventario_kardex",
            "inventario_consumos_registro",
            "compra",
            "detalle_compra",
            "proveedores",
            "animal",
            "historia_clinica",
            "consulta",
            "tratamientos",
            "peticiones",
            "seguimiento_peticiones_visita",
            "visita_animal",
        ]

        # Comprobar que todas las tablas de negocio tienen datos antes del teardown
        with connection.cursor() as cursor:
            for tabla in tablas_negocio:
                cursor.execute(f"SELECT COUNT(*) FROM public.{tabla}")
                count_pre = cursor.fetchone()[0]
                self.assertGreater(
                    count_pre,
                    0,
                    f"Precondición fallida: la tabla {tabla} debía tener filas antes del teardown",
                )

        # Invocar explícitamente el teardown dinámico
        self._fixture_teardown()

        # Comprobar 0 filas en cada tabla de negocio operacional tras el teardown
        with connection.cursor() as cursor:
            for tabla in tablas_negocio:
                cursor.execute(f"SELECT COUNT(*) FROM public.{tabla}")
                count_post = cursor.fetchone()[0]
                self.assertEqual(
                    count_post,
                    0,
                    f"Fallo de limpieza: la tabla {tabla} debía quedar con 0 filas tras el teardown, pero tiene {count_post}",
                )

        # Comprobar catálogos base resembrados
        catalogos_esperados = {
            "rol": 4,
            "tipo_peticion": 1,
            "estado_peticiones": 2,
            "especies": 1,
            "raza": 1,
        }
        with connection.cursor() as cursor:
            for tabla_cat, count_esperado in catalogos_esperados.items():
                cursor.execute(f"SELECT COUNT(*) FROM public.{tabla_cat}")
                count_cat = cursor.fetchone()[0]
                self.assertEqual(
                    count_cat,
                    count_esperado,
                    f"Fallo de re-siembra: el catálogo {tabla_cat} debía tener {count_esperado} filas, pero tiene {count_cat}",
                )

        # Comprobar reinicio de secuencias (crear usuario nuevo debe generar id_usuario=1)
        nuevo_user = crear_usuario(rol_id=1, email="primer_usuario_post_teardown@cba.gov.co")
        self.assertEqual(
            nuevo_user.id_usuario,
            1,
            f"Fallo de reinicio de secuencia: nuevo usuario debía tener id_usuario=1 tras teardown, obtuvo {nuevo_user.id_usuario}",
        )
