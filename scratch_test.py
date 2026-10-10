import sys
from django.db import transaction
from rest_framework.exceptions import ValidationError as DRFValidationError
from inventario.models import Proveedores, Compra, DetalleCompra, DetalleSalida, Inventarios
from medicamentos.models import Medicamentos, AdministracionMedicamento
from users.models import Usuarios
from inventario.api.serializers import CompraSerializer, DetalleSalidaSerializer

def run_tests():
    try:
        with transaction.atomic():
            print("--- INICIANDO PRUEBAS ---")
            proveedor = Proveedores.objects.first()
            if not proveedor: 
                proveedor = Proveedores.objects.create(nombre="Test Prov")
                
            usuario = Usuarios.objects.first()
            if not usuario: 
                usuario = Usuarios.objects.create(nombre="Test User", id_rol_id=1)
                
            # Crear un medicamento completamente nuevo para aislar la prueba de la BD existente
            import time
            medicamento = Medicamentos.objects.create(nombre=f"Test Med AisLado {time.time()}")
            from clinica.models import SeguimientoHospitalario
            seguimiento = SeguimientoHospitalario.objects.first()
            if not seguimiento:
                # Mock if none exists
                seguimiento = SeguimientoHospitalario.objects.create(motivo="Test")
            administracion = AdministracionMedicamento.objects.create(id_medicamento=medicamento, id_seguimiento_hosp=seguimiento)
            
            print("Setup completado aislando medicamento.")
            
            compra_data = {
                "id_proveedor": proveedor.pk, 
                "detalles": [
                    {"id_medicamento": medicamento.pk, "cantidad": 10, "precio_unitario": 100}, 
                    {"id_medicamento": medicamento.pk, "cantidad": 5, "precio_unitario": 120}
                ]
            }
            
            print("\n>> Probando Creacion de Compra...")
            cs = CompraSerializer(data=compra_data)
            cs.is_valid(raise_exception=True)
            compra = cs.save()
            
            invs = Inventarios.objects.filter(id_detalle_compra__id_compra=compra.pk).order_by("id_inventario")
            for i in invs: 
                print(f"Lote ID {i.pk} | Cantidad Actual: {i.cantidad_actual}")
                
            print("\n>> Probando Salida FIFO de 12 uds...")
            sd = {"id_usuario": usuario.pk, "id_administracion": administracion.pk, "cantidad": 12}
            ss = DetalleSalidaSerializer(data=sd)
            ss.is_valid(raise_exception=True)
            ss.save()
            
            i1 = Inventarios.objects.get(pk=invs[0].pk)
            i2 = Inventarios.objects.get(pk=invs[1].pk)
            print(f"Lote 1 (era 10) ahora: {i1.cantidad_actual}")
            print(f"Lote 2 (era 5) ahora: {i2.cantidad_actual}")
            
            print("\n>> Probando Salida Fallida (Stock) de 5 uds...")
            sfd = {"id_usuario": usuario.pk, "id_administracion": administracion.pk, "cantidad": 5}
            sfs = DetalleSalidaSerializer(data=sfd)
            try:
                sfs.is_valid(raise_exception=True)
                sfs.save()
                print("ERROR: Dejo pasar una salida sin stock!")
            except DRFValidationError as e:
                print(f"Exito: Bloqueado correctamente. Error devuelto: {e.detail}")
                
            raise Exception("ROLLBACK")
    except Exception as e:
        if str(e) == "ROLLBACK": 
            print("\nLimpieza DB (Rollback intencional) OK.")
        else: 
            print(f"\nERROR DURANTE LA PRUEBA: {e}")

run_tests()
