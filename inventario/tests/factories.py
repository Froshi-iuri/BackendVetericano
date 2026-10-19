"""
Factorias dinamicas para fixtures de prueba y resiembra de catalogos base.
Respetan estrictamente secuencias de PostgreSQL (nextval y setval) sin hardcodear IDs fijos.
"""

import uuid
from django.db import connection
from django.utils import timezone
from users.models import Usuarios, Rol
from medicamentos.models import Medicamentos
from inventario.models import (
    Proveedores,
    Compra,
    DetalleCompra,
    Inventarios,
    KitVeterinarioLotes,
)
from animales.models import Animal
from clinica.models import HistoriaClinica, Consulta
from peticiones.models import Peticiones, TipoPeticion, EstadoPeticiones
from especies.models import Especie, Raza


def sembrar_catalogos_base():
    """
    Resiembra los catalogos base indispensables para las pruebas y sincroniza
    dinamicamente las secuencias de PostgreSQL mediante pg_get_serial_sequence.
    """
    with connection.cursor() as cur:
        # 1. rol
        roles = [
            (1, 'Administrador'),
            (2, 'Jurídico'),
            (3, 'Veterinario'),
            (4, 'Peticionario'),
        ]
        for r_id, r_name in roles:
            cur.execute(
                "INSERT INTO public.rol (id_rol, nombre_rol) VALUES (%s, %s) "
                "ON CONFLICT (id_rol) DO UPDATE SET nombre_rol = EXCLUDED.nombre_rol;",
                (r_id, r_name)
            )
        cur.execute("SELECT setval(pg_get_serial_sequence('rol', 'id_rol'), 4);")

        # 2. tipo_peticion
        cur.execute(
            "INSERT INTO public.tipo_peticion (id_tipo, nombre, activo) VALUES (%s, %s, %s) "
            "ON CONFLICT (id_tipo) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
            (1, 'Atención Animal Campo', True)
        )
        cur.execute("SELECT setval(pg_get_serial_sequence('tipo_peticion', 'id_tipo'), 1);")

        # 3. estado_peticiones
        estados = [
            (1, 'En Proceso'),
            (2, 'Atendida'),
        ]
        for e_id, e_name in estados:
            cur.execute(
                "INSERT INTO public.estado_peticiones (id_estado, nombre, activo) VALUES (%s, %s, %s) "
                "ON CONFLICT (id_estado) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
                (e_id, e_name, True)
            )
        cur.execute("SELECT setval(pg_get_serial_sequence('estado_peticiones', 'id_estado'), 2);")

        # 4. especies y raza
        cur.execute(
            "INSERT INTO public.especies (id_especie, nombre, activo) VALUES (%s, %s, %s) "
            "ON CONFLICT (id_especie) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
            (1, 'Canino', True)
        )
        cur.execute("SELECT setval(pg_get_serial_sequence('especies', 'id_especie'), 1);")

        cur.execute(
            "INSERT INTO public.raza (id_raza, id_especie, nombre, activo) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id_raza) DO UPDATE SET nombre = EXCLUDED.nombre, activo = EXCLUDED.activo;",
            (1, 1, 'Mestizo Canino', True)
        )
        cur.execute("SELECT setval(pg_get_serial_sequence('raza', 'id_raza'), 1);")


def crear_usuario(rol_id=3, email=None, activo=True, nombre="Test", apellido="User", password="password123", identificacion=None):
    """Crea una instancia de Usuarios con campos unicos generados dinamicamente."""
    uid = uuid.uuid4().hex[:8]
    if not email:
        email = f"user_{uid}@cba.gov.co"
    if not identificacion:
        identificacion = f"ID-{uid}"

    return Usuarios.objects.create(
        id_rol_id=rol_id,
        nombre=nombre,
        apellido=apellido,
        email=email,
        identificacion=identificacion,
        password=password,
        activo=activo
    )


def crear_medicamento(nombre=None, activo=True, principio_activo="Principio Activo", presentacion="Frasco", concentracion="100mg", tipo="Inyectable"):
    """Crea una instancia del catalogo de Medicamentos."""
    if not nombre:
        nombre = f"Medicamento {uuid.uuid4().hex[:6]}"

    return Medicamentos.objects.create(
        nombre=nombre,
        principio_activo=principio_activo,
        presentacion=presentacion,
        concentracion=concentracion,
        tipo=tipo,
        activo=activo
    )


def crear_lote_bodega(medicamento, cantidad=50, fecha_vencimiento=None, fecha_compra=None, estado="Disponible"):
    """
    Crea un lote fisico en bodega CBA (Inventarios) con su respectiva Compra y DetalleCompra.
    """
    prov, _ = Proveedores.objects.get_or_create(
        nombre="Proveedor Principal CBA",
        defaults={"telefono": "1234567", "email": "proveedor@cba.gov.co", "activo": True}
    )

    compra = Compra.objects.create(
        id_proveedor=prov
    )
    if fecha_compra is not None:
        Compra.objects.filter(pk=compra.pk).update(fecha=fecha_compra)
        compra.refresh_from_db()

    detalle = DetalleCompra.objects.create(
        id_compra=compra,
        id_medicamento=medicamento,
        cantidad=cantidad
    )

    return Inventarios.objects.create(
        id_detalle_compra=detalle,
        cantidad_actual=cantidad,
        fecha_vencimiento=fecha_vencimiento,
        estado=estado
    )


def crear_lote_kit(veterinario, lote_bodega, cantidad=10, cantidad_ref=10, fecha_vencimiento=None, fecha_compra_origen=None):
    """
    Crea o asigna una tupla en KitVeterinarioLotes enlazada a un lote de bodega CBA.
    """
    if fecha_vencimiento is None:
        fecha_vencimiento = lote_bodega.fecha_vencimiento

    if fecha_compra_origen is None and lote_bodega.id_detalle_compra_id:
        fecha_compra_origen = lote_bodega.id_detalle_compra.id_compra.fecha

    return KitVeterinarioLotes.objects.create(
        id_veterinario=veterinario,
        id_inventario=lote_bodega,
        id_medicamento=lote_bodega.id_detalle_compra.id_medicamento,
        cantidad_actual=cantidad,
        cantidad_referencia=cantidad_ref,
        fecha_vencimiento=fecha_vencimiento,
        fecha_compra_origen=fecha_compra_origen
    )


def crear_animal_con_historia(especie_id=1, raza_id=1, nombre="Kira", sexo="Hembra"):
    """
    Crea una tupla de Animal con su correspondiente HistoriaClinica.
    """
    animal = Animal.objects.create(
        id_raza_id=raza_id,
        nombre=nombre,
        sexo=sexo,
        peso=10.0,
        activo=True
    )
    historia = HistoriaClinica.objects.create(
        id_animal=animal,
        numero_historia=f"HC-{animal.id_animal:05d}"
    )
    return animal, historia


def crear_peticion_visita(peticionario=None, veterinario=None, tipo_id=1, estado_id=1, radicado=None):
    """
    Crea una solicitud de visita de campo en Peticiones.
    """
    if peticionario is None:
        peticionario = crear_usuario(rol_id=4, nombre="Ciudadano", apellido="Peticionario")

    if not radicado:
        radicado = f"RAD-{uuid.uuid4().hex[:8].upper()}"

    return Peticiones.objects.create(
        numero_radicado=radicado,
        id_tipo_id=tipo_id,
        id_estado_id=estado_id,
        responsable=peticionario,
        asignado_a=veterinario,
        descripcion="Visita de atencion medica en campo requerida",
        prioridad="Media"
    )


def crear_consulta(historia=None, motivo_consulta="Consulta de control veterinario"):
    """
    Crea una tupla de Consulta clinica vinculada a una HistoriaClinica.
    """
    if historia is None:
        _, historia = crear_animal_con_historia()
    return Consulta.objects.create(
        id_historia=historia,
        motivo_consulta=motivo_consulta,
    )
