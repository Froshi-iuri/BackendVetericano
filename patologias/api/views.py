from django.db import transaction
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from patologias.models import Patologia
from users.api.permissions import EsAdminOReadOnlyParaStaff
from .serializers import PatologiaSerializer


def _normalizar_especie_filtro(valor):
    esp = (valor or '').strip().lower()
    if not esp:
        return None
    if any(w in esp for w in ('perro', 'canino', 'dog')) or esp == 'can':
        return 'canino'
    if any(w in esp for w in ('gato', 'felino', 'cat')) or esp == 'fel':
        return 'felino'
    if esp == 'ambos':
        return 'ambos'
    return 'desconocida'


def _nombres_por_especie_ia(especie_canonica):
    from diagnostico_ia.ml.knowledge_base import PATOLOGIAS_KNOWLEDGE
    if especie_canonica == 'ambos':
        return [
            nombre for nombre, info in PATOLOGIAS_KNOWLEDGE.items()
            if info.get('especie') == 'ambos'
        ]
    if especie_canonica in ('canino', 'felino'):
        return [
            nombre for nombre, info in PATOLOGIAS_KNOWLEDGE.items()
            if info.get('especie') in (especie_canonica, 'ambos')
        ]
    return []


def _extraer_lista_items(data, claves_contenedor=('items', 'patologias', 'registros', 'datos')):
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        for clave in claves_contenedor:
            if isinstance(data.get(clave), list):
                return data[clave], None
        return [data], None
    return None, "El cuerpo de la solicitud debe ser una lista de objetos o un objeto JSON."


def _normalizar_texto(valor):
    if valor is None:
        return ''
    return ' '.join(str(valor).split())


def _obtener_valor_normalizado(item_lower, *claves):
    for clave in claves:
        if clave in item_lower and item_lower[clave] is not None:
            return item_lower[clave]
    return None


class PatologiaViewSet(viewsets.ModelViewSet):
    queryset = Patologia.objects.all().order_by('nombre')
    serializer_class = PatologiaSerializer
    permission_classes = [EsAdminOReadOnlyParaStaff]

    def get_queryset(self):
        qs = super().get_queryset()
        params = getattr(self.request, 'query_params', None)
        if not params:
            return qs

        tipo = (params.get('tipo_de_enfermedad') or params.get('clasificacion') or '').strip()
        if tipo:
            qs = qs.filter(tipo_de_enfermedad__iexact=tipo)

        activo_raw = (params.get('activo') or '').strip().lower()
        if activo_raw in ('true', '1', 'activo', 'si', 'sí'):
            qs = qs.filter(activo=True)
        elif activo_raw in ('false', '0', 'inactivo', 'no'):
            qs = qs.filter(activo=False)

        search = (params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(nombre__icontains=search)
                | Q(descripcion__icontains=search)
                | Q(tipo_de_enfermedad__icontains=search)
            )

        especie_raw = params.get('especie')
        especie_canonica = _normalizar_especie_filtro(especie_raw)
        if especie_canonica is not None:
            nombres = _nombres_por_especie_ia(especie_canonica)
            qs = qs.filter(nombre__in=nombres) if nombres else qs.none()

        return qs

    def create(self, request, *args, **kwargs):
        is_many = isinstance(request.data, list)
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=['post'], url_path='carga-masiva')
    def carga_masiva(self, request):
        """
        Permite la carga masiva de patologías desde JSON o lista de objetos.
        Evita duplicados por nombre (insensible a mayúsculas/espacios) y preserva
        clasificación, descripción y estado existentes cuando no se envían en la fila.
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
            nombre = _normalizar_texto(_obtener_valor_normalizado(item_lower, 'nombre'))
            if not nombre:
                errores.append(f"Fila {idx}: El nombre es obligatorio.")
                continue
            if len(nombre) > 150:
                errores.append(f"Fila {idx}: El nombre '{nombre[:30]}...' excede los 150 caracteres.")
                continue

            raw_desc = _obtener_valor_normalizado(item_lower, 'descripcion')
            descripcion = str(raw_desc).strip() if raw_desc is not None else ''

            raw_tipo = _obtener_valor_normalizado(
                item_lower, 'tipo_de_enfermedad', 'tipo de enfermedad', 'clasificacion', 'tipo'
            )
            tipo = _normalizar_texto(raw_tipo)
            if len(tipo) > 100:
                errores.append(f"Fila {idx}: 'tipo_de_enfermedad' excede los 100 caracteres.")
                continue

            raw_activo = _obtener_valor_normalizado(item_lower, 'activo', 'estado')
            activo_norm = None
            if raw_activo is not None and str(raw_activo).strip() != '':
                if isinstance(raw_activo, bool):
                    activo_norm = raw_activo
                elif isinstance(raw_activo, (int, float)) and raw_activo in (0, 1):
                    activo_norm = bool(raw_activo)
                else:
                    val_act = str(raw_activo).strip().lower()
                    if val_act in ('activo', 'true', '1', 'si', 'sí'):
                        activo_norm = True
                    elif val_act in ('inactivo', 'false', '0', 'no'):
                        activo_norm = False
                    else:
                        errores.append(f"Fila {idx}: Estado '{raw_activo}' no reconocido.")
                        continue

            try:
                with transaction.atomic():
                    existente = (
                        Patologia.objects.filter(nombre__iexact=nombre)
                        .order_by('id_patologia')
                        .first()
                    )
                    if existente is not None:
                        campos_modificados = []
                        if descripcion and existente.descripcion != descripcion:
                            existente.descripcion = descripcion
                            campos_modificados.append('descripcion')
                        if tipo and existente.tipo_de_enfermedad != tipo:
                            existente.tipo_de_enfermedad = tipo
                            campos_modificados.append('tipo_de_enfermedad')
                        if activo_norm is not None and existente.activo != activo_norm:
                            existente.activo = activo_norm
                            campos_modificados.append('activo')
                        if campos_modificados:
                            existente.save(update_fields=campos_modificados)
                        actualizados.append(existente.nombre)
                    else:
                        nuevo = Patologia.objects.create(
                            nombre=nombre,
                            descripcion=descripcion or None,
                            tipo_de_enfermedad=tipo or None,
                            activo=True if activo_norm is None else activo_norm,
                        )
                        creados.append(nuevo.nombre)
            except Exception as exc:
                errores.append(f"Fila {idx} ({nombre}): {exc}")

        ok_con_cambios = not errores or len(creados) > 0 or len(actualizados) > 0
        return Response({
            'mensaje': f'Proceso completado. Creados: {len(creados)}, Actualizados: {len(actualizados)}, Errores: {len(errores)}',
            'creados_count': len(creados),
            'actualizados_count': len(actualizados),
            'errores': errores
        }, status=status.HTTP_200_OK if ok_con_cambios else status.HTTP_400_BAD_REQUEST)
