from django.db import transaction
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from examenes_clinicos.models import Examen, ProcedimientoRealizado
from users.api.permissions import EsAdminOReadOnlyParaStaff, PermisoClinica
from .serializers import ExamenSerializer, ProcedimientoRealizadoSerializer


def _extraer_lista_items(data, claves_contenedor=('items', 'examenes', 'registros', 'datos')):
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        for clave in claves_contenedor:
            if isinstance(data.get(clave), list):
                return data[clave], None
        return [data], None
    return None, "El cuerpo de la solicitud debe ser una lista de objetos o un objeto JSON."


def _obtener_valor_normalizado(item_lower, *claves):
    for clave in claves:
        if clave in item_lower and item_lower[clave] is not None:
            return item_lower[clave]
    return None


def _normalizar_texto(valor):
    if valor is None:
        return ''
    return ' '.join(str(valor).split())


class ExamenViewSet(viewsets.ModelViewSet):
    queryset = Examen.objects.all().order_by('-id_examen')
    serializer_class = ExamenSerializer
    permission_classes = [EsAdminOReadOnlyParaStaff]

    def get_queryset(self):
        qs = super().get_queryset()
        params = getattr(self.request, 'query_params', None)
        if not params:
            return qs

        search = (params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(nombre_tipo_examen__icontains=search)
                | Q(descripcion_hallazgos__icontains=search)
                | Q(ruta_archivo_resultado__icontains=search)
            )

        estado = (params.get('estado') or '').strip().lower()
        if estado in ('activo', 'true', '1'):
            qs = qs.filter(estado__iexact='activo')
        elif estado in ('inactivo', 'false', '0'):
            qs = qs.filter(estado__iexact='inactivo')

        tipo = (params.get('tipo') or '').strip()
        if tipo:
            qs = qs.filter(ruta_archivo_resultado__iexact=tipo)

        solo_catalogo = (params.get('solo_catalogo') or '').strip().lower()
        if solo_catalogo in ('true', '1', 'si', 'sí'):
            qs = qs.filter(id_consulta__isnull=True)
        elif solo_catalogo in ('false', '0', 'no'):
            qs = qs.filter(id_consulta__isnull=False)

        id_consulta = (params.get('id_consulta') or '').strip()
        if id_consulta:
            if id_consulta.isdigit():
                qs = qs.filter(id_consulta_id=int(id_consulta))
            else:
                return qs.none()

        return qs

    def create(self, request, *args, **kwargs):
        is_many = isinstance(request.data, list)
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    @action(detail=False, methods=['post'], url_path='carga-masiva')
    def carga_masiva(self, request):
        """
        Permite la carga masiva de exámenes clínicos desde JSON o lista de objetos.
        Evita duplicados por nombre (insensible a mayúsculas/espacios) y preserva
        valores existentes cuando las columnas opcionales vienen vacías.
        """
        items, error_formato = _extraer_lista_items(request.data)
        if error_formato:
            return Response({'errores': [error_formato]}, status=status.HTTP_400_BAD_REQUEST)
        if not items:
            return Response(
                {'errores': ['No se enviaron registros para procesar.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        creados = []
        actualizados = []
        errores = []

        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                errores.append(f"Fila {idx}: Formato inválido, se esperaba un objeto.")
                continue

            item_lower = {
                str(k).strip().lower().replace('ó', 'o').replace('í', 'i'): v
                for k, v in item.items()
            }
            nombre = _normalizar_texto(_obtener_valor_normalizado(
                item_lower, 'nombre', 'nombre_tipo_examen', 'nombre_tipo'
            ))
            if not nombre:
                errores.append(f"Fila {idx}: El nombre del examen es obligatorio.")
                continue
            if len(nombre) > 100:
                errores.append(f"Fila {idx}: El nombre '{nombre[:30]}...' excede los 100 caracteres.")
                continue

            raw_desc = _obtener_valor_normalizado(item_lower, 'descripcion', 'descripcion_hallazgos')
            descripcion = str(raw_desc).strip() if raw_desc is not None else ''

            raw_tipo = _obtener_valor_normalizado(item_lower, 'tipo', 'ruta_archivo_resultado')
            tipo = _normalizar_texto(raw_tipo)
            if len(tipo) > 500:
                errores.append(f"Fila {idx}: El tipo/especialidad excede los 500 caracteres.")
                continue

            raw_estado = _obtener_valor_normalizado(item_lower, 'estado', 'activo')
            estado_norm = None
            if raw_estado is not None and str(raw_estado).strip() != '':
                if isinstance(raw_estado, bool):
                    estado_norm = 'activo' if raw_estado else 'inactivo'
                else:
                    val_est = str(raw_estado).strip().lower()
                    if val_est in ('true', '1', 'si', 'sí', 'activo'):
                        estado_norm = 'activo'
                    elif val_est in ('false', '0', 'no', 'inactivo'):
                        estado_norm = 'inactivo'
                    else:
                        errores.append(f"Fila {idx}: Estado '{raw_estado}' no reconocido (use activo/inactivo).")
                        continue

            try:
                with transaction.atomic():
                    existente = (
                        Examen.objects.filter(
                            id_consulta__isnull=True,
                            nombre_tipo_examen__iexact=nombre,
                        )
                        .order_by('id_examen')
                        .first()
                    )
                    if existente is not None:
                        campos_modificados = []
                        if descripcion and existente.descripcion_hallazgos != descripcion:
                            existente.descripcion_hallazgos = descripcion
                            campos_modificados.append('descripcion_hallazgos')
                        if tipo and existente.ruta_archivo_resultado != tipo:
                            existente.ruta_archivo_resultado = tipo
                            campos_modificados.append('ruta_archivo_resultado')
                        if estado_norm is not None and existente.estado != estado_norm:
                            existente.estado = estado_norm
                            campos_modificados.append('estado')
                        if campos_modificados:
                            existente.save(update_fields=campos_modificados)
                        actualizados.append(existente.nombre_tipo_examen)
                    else:
                        nuevo = Examen.objects.create(
                            nombre_tipo_examen=nombre,
                            descripcion_hallazgos=descripcion or None,
                            ruta_archivo_resultado=tipo or 'Prueba Diagnóstica',
                            estado=estado_norm or 'activo',
                            solicitado=True,
                        )
                        creados.append(nuevo.nombre_tipo_examen)
            except Exception as exc:
                errores.append(f"Fila {idx} ({nombre}): {exc}")

        ok_con_cambios = not errores or len(creados) > 0 or len(actualizados) > 0
        return Response({
            'mensaje': f'Proceso completado. Creados: {len(creados)}, Actualizados: {len(actualizados)}, Errores: {len(errores)}',
            'creados_count': len(creados),
            'actualizados_count': len(actualizados),
            'errores': errores
        }, status=status.HTTP_200_OK if ok_con_cambios else status.HTTP_400_BAD_REQUEST)


class ProcedimientoRealizadoViewSet(viewsets.ModelViewSet):
    queryset = ProcedimientoRealizado.objects.all()
    serializer_class = ProcedimientoRealizadoSerializer
    permission_classes = [PermisoClinica]
