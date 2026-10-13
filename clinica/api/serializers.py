from rest_framework import serializers
from clinica.models import (
    HistoriaClinica,
    Consulta,
    HospitalizacionSeresSintientes,
    Tratamientos,
    Diagnostico,
    SeguimientosClinicos,
    DiagnosticoPatologia,
    SeguimientoHospitalario,
)


class HistoriaClinicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoriaClinica
        fields = '__all__'


class ConsultaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Consulta
        fields = '__all__'


class HospitalizacionSeresSintientesSerializer(serializers.ModelSerializer):
    class Meta:
        model = HospitalizacionSeresSintientes
        fields = '__all__'


class TratamientosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tratamientos
        fields = '__all__'


class DiagnosticoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Diagnostico
        fields = '__all__'


class SeguimientosClinicosSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeguimientosClinicos
        fields = '__all__'


class DiagnosticoPatologiaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticoPatologia
        fields = '__all__'


class SeguimientoHospitalarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeguimientoHospitalario
        fields = '__all__'


from django.db import transaction
from animales.models import Animal, Responsables, AnimalResponsable
from especies.models import Especie, Raza
from datetime import datetime


# ---------------------------------------------------------------------------
# Descuento de inventario desde consultas clínicas
# Replica la misma lógica de lotes/FIFO de inventario.api.DetalleSalidaSerializer
# sin modificar el módulo de inventario.
# ---------------------------------------------------------------------------
def _filtro_lotes_medicamento(id_medicamento):
    from django.db.models import Q
    from inventario.models import Inventarios
    return Inventarios.objects.filter(
        Q(id_detalle_compra__id_medicamento_id=id_medicamento) |
        Q(id_detalle_salida__id_administracion__id_medicamento_id=id_medicamento),
        cantidad_actual__gt=0
    )


def _stock_disponible(id_medicamento):
    from django.db.models import Sum
    return _filtro_lotes_medicamento(id_medicamento).aggregate(
        total=Sum('cantidad_actual')
    )['total'] or 0


def _parse_cantidad_usada(valor):
    """Devuelve un entero >= 0 o lanza ValueError si el valor no es un entero válido."""
    if valor in (None, ''):
        return 0
    if isinstance(valor, bool):
        raise ValueError
    numero = float(valor)
    if numero < 0 or not numero.is_integer():
        raise ValueError
    return int(numero)


def _debe_descontar(item):
    """Solo descuenta si trae id_medicamento, cantidad_usada > 0 y no se marcó descontar_inventario=false."""
    if item.get('descontar_inventario') is False:
        return False
    return bool(item.get('id_medicamento')) and item.get('_cantidad_usada', 0) > 0


def _descontar_fifo(id_medicamento, cantidad):
    """Descuenta `cantidad` de los lotes más antiguos. Debe ejecutarse dentro de transaction.atomic."""
    from inventario.models import Inventarios
    ids_lotes = list(_filtro_lotes_medicamento(id_medicamento).values_list('id_inventario', flat=True))
    # Bloqueo de filas para evitar descuentos concurrentes sobre el mismo lote
    lotes = list(
        Inventarios.objects.select_for_update()
        .filter(pk__in=ids_lotes, cantidad_actual__gt=0)
        .order_by('id_inventario')
    )
    total = sum(lote.cantidad_actual for lote in lotes)
    if cantidad > total:
        return False, total

    por_descontar = cantidad
    for lote in lotes:
        if por_descontar <= 0:
            break
        if lote.cantidad_actual >= por_descontar:
            lote.cantidad_actual -= por_descontar
            por_descontar = 0
        else:
            por_descontar -= lote.cantidad_actual
            lote.cantidad_actual = 0
        lote.save(update_fields=['cantidad_actual'])
    return True, total

class ExpedienteCompletoSerializer(serializers.Serializer):
    id_animal = serializers.IntegerField(required=False, allow_null=True)
    animal = serializers.JSONField(required=False, allow_null=True)
    tenedor = serializers.JSONField(required=False, allow_null=True)
    anamnesicos = serializers.JSONField(required=False, allow_null=True)
    motivoConsulta = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    examenClinico = serializers.JSONField(required=False, allow_null=True)
    organosSistemas = serializers.JSONField(required=False, allow_null=True)
    hoja2 = serializers.JSONField(required=False, allow_null=True)
    hoja3 = serializers.JSONField(required=False, allow_null=True)
    rescate = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        id_animal = attrs.get('id_animal')
        animal_data = attrs.get('animal') or {}
        motivo = attrs.get('motivoConsulta')

        tiene_id_animal = bool(id_animal or (isinstance(animal_data, dict) and (animal_data.get('id_animal') or animal_data.get('id'))))
        tiene_nombre_animal = bool(isinstance(animal_data, dict) and str(animal_data.get('nombre', '')).strip())

        if not tiene_id_animal and not tiene_nombre_animal:
            raise serializers.ValidationError({
                "animal": "Debe especificar el id_animal existente o ingresar al menos el nombre del animal a registrar."
            })

        if not motivo or not str(motivo).strip():
            raise serializers.ValidationError({
                "motivoConsulta": "El motivo de la consulta es obligatorio para registrar la atención médica."
            })

        self._validar_medicamentos_tratamientos(attrs)

        return attrs

    def _validar_medicamentos_tratamientos(self, attrs):
        """
        Valida los tratamientos que traen id_medicamento / cantidad_usada.
        Los tratamientos solo-texto (flujo actual) no se validan ni descuentan.
        """
        from medicamentos.models import Medicamentos

        hoja2 = attrs.get('hoja2') or {}
        tratamientos = hoja2.get('tratamientos') if isinstance(hoja2, dict) else None
        if not isinstance(tratamientos, list):
            return

        errores = []
        solicitados = {}  # id_medicamento -> cantidad total a descontar

        for idx, item in enumerate(tratamientos, start=1):
            if not isinstance(item, dict):
                continue
            id_med = item.get('id_medicamento')

            try:
                item['_cantidad_usada'] = _parse_cantidad_usada(item.get('cantidad_usada'))
            except (ValueError, TypeError):
                errores.append(f"Tratamiento #{idx}: 'cantidad_usada' debe ser un número entero mayor o igual a 0.")
                continue

            if item.get('descontar_inventario') is True:
                if not id_med:
                    errores.append(f"Tratamiento #{idx}: para descontar inventario se requiere 'id_medicamento'.")
                    continue
                if item['_cantidad_usada'] <= 0:
                    errores.append(f"Tratamiento #{idx}: para descontar inventario 'cantidad_usada' debe ser mayor a 0.")
                    continue

            if id_med:
                try:
                    id_med = int(id_med)
                except (ValueError, TypeError):
                    errores.append(f"Tratamiento #{idx}: 'id_medicamento' inválido.")
                    continue
                item['id_medicamento'] = id_med
                if not Medicamentos.objects.filter(pk=id_med).exists():
                    errores.append(f"Tratamiento #{idx}: el medicamento con id {id_med} no existe.")
                    continue

            if _debe_descontar(item):
                solicitados[id_med] = solicitados.get(id_med, 0) + item['_cantidad_usada']

        # Validación de stock (sumando si el mismo medicamento viene en varias líneas)
        for id_med, cantidad in solicitados.items():
            disponible = _stock_disponible(id_med)
            if cantidad > disponible:
                med = Medicamentos.objects.filter(pk=id_med).first()
                nombre = med.nombre if med else id_med
                errores.append(
                    f"Stock insuficiente para '{nombre}'. Disponibles: {disponible}, solicitados: {cantidad}."
                )

        if errores:
            raise serializers.ValidationError({"tratamientos": errores})

    def create(self, validated_data):
        with transaction.atomic():
            animal_data = validated_data.get('animal') or {}
            tenedor_data = validated_data.get('tenedor') or {}
            anamnesicos_data = validated_data.get('anamnesicos') or {}
            examen_data = validated_data.get('examenClinico') or {}
            organos_data = validated_data.get('organosSistemas') or {}
            hoja2_data = validated_data.get('hoja2') or {}
            hoja3_data = validated_data.get('hoja3') or {}

            # Extraer id_animal si fue enviado directamente o dentro de 'animal'
            id_animal = validated_data.get('id_animal')
            if not id_animal and isinstance(animal_data, dict):
                id_animal = animal_data.get('id_animal') or animal_data.get('id')

            animal_obj = None
            if id_animal:
                animal_obj = Animal.objects.filter(pk=id_animal).first()

            if not animal_obj:
                # 1. Especie y Raza (Búsqueda tolerante a duplicados con .filter().first())
                especie_nombre = animal_data.get('especie', 'Desconocida')
                if especie_nombre == 'Otro':
                    especie_nombre = animal_data.get('otraEspecie', 'Otra')
                especie_obj = Especie.objects.filter(nombre__iexact=especie_nombre).first()
                if not especie_obj:
                    especie_obj = Especie.objects.create(nombre=especie_nombre)
                
                raza_nombre = animal_data.get('raza', 'Mestizo')
                if not raza_nombre:
                    raza_nombre = 'Mestizo'
                raza_obj = Raza.objects.filter(nombre__iexact=raza_nombre, id_especie=especie_obj).first()
                if not raza_obj:
                    raza_obj = Raza.objects.create(nombre=raza_nombre, id_especie=especie_obj)

                # 2. Crear nuevo Animal
                animal_obj = Animal.objects.create(
                    id_raza=raza_obj,
                    nombre=animal_data.get('nombre', 'Sin Nombre'),
                    sexo=animal_data.get('sexo', ''),
                    color=animal_data.get('color', ''),
                    peso=animal_data.get('peso') if animal_data.get('peso') else None,
                    observaciones=animal_data.get('descripcionBreve', ''),
                    caracteristicas=animal_data.get('caracteristicasEspeciales', ''),
                    foto_url=animal_data.get('fotoUrl', '')
                )
            else:
                # Si el animal ya existe, actualizar peso si viene un dato nuevo
                peso_val = animal_data.get('peso')
                if peso_val is not None:
                    try:
                        animal_obj.peso = float(peso_val)
                        animal_obj.save(update_fields=['peso'])
                    except (ValueError, TypeError):
                        pass

            # 3. Responsable
            doc_identidad = tenedor_data.get('documentoIdentidad', '')
            if doc_identidad:
                responsable_obj = Responsables.objects.filter(documento=doc_identidad).first()
                if not responsable_obj:
                    responsable_obj = Responsables.objects.create(
                        documento=doc_identidad,
                        nombre=tenedor_data.get('nombres', 'Sin Nombre'),
                        telefono=tenedor_data.get('telefono', ''),
                        correo=tenedor_data.get('correoElectronico', ''),
                        direccion=tenedor_data.get('direccion', ''),
                    )
                if not AnimalResponsable.objects.filter(id_animal=animal_obj, id_responsable=responsable_obj).exists():
                    AnimalResponsable.objects.create(
                        id_animal=animal_obj,
                        id_responsable=responsable_obj,
                        tipo_responsabilidad='Tenedor'
                    )

            # 4. Historia Clínica (Unicidad garantizada: 1 animal = 1 historia)
            def parse_date(date_str):
                if not date_str: return None
                try:
                    return datetime.fromisoformat(str(date_str).replace('Z', '')).date()
                except:
                    return None

            historia = HistoriaClinica.objects.filter(id_animal=animal_obj).first()
            if historia:
                # Actualizar datos de antecedentes si fueron suministrados
                if anamnesicos_data.get('ultimaDesparasitacion'):
                    historia.fecha_ultima_desparasitacion = parse_date(anamnesicos_data.get('ultimaDesparasitacion'))
                if anamnesicos_data.get('productoDesparasitacion'):
                    historia.producto_desparasitacion = anamnesicos_data.get('productoDesparasitacion')
                if anamnesicos_data.get('vacunas'):
                    historia.vacunas = anamnesicos_data.get('vacunas')
                if anamnesicos_data.get('enfermedadesAnteriores'):
                    historia.enfermedades_anteriores = anamnesicos_data.get('enfermedadesAnteriores')
                if anamnesicos_data.get('tratamientos'):
                    historia.tratamientos_anteriores = anamnesicos_data.get('tratamientos')
                if anamnesicos_data.get('evolucion'):
                    historia.evolucion_previa = anamnesicos_data.get('evolucion')
                if anamnesicos_data.get('alimentacion'):
                    historia.alimentacion = anamnesicos_data.get('alimentacion')
                if anamnesicos_data.get('historiaReproductiva'):
                    historia.estado_reproductivo = anamnesicos_data.get('historiaReproductiva')
                if anamnesicos_data.get('ultimoCelo'):
                    historia.fecha_ultimo_celo = parse_date(anamnesicos_data.get('ultimoCelo'))
                if anamnesicos_data.get('fechaUltimoParto'):
                    historia.fecha_ultimo_parto = parse_date(anamnesicos_data.get('fechaUltimoParto'))
                num_hc = animal_data.get('numeroHistoriaClinica')
                if num_hc:
                    historia.numero_historia = num_hc
                historia.save()
            else:
                historia = HistoriaClinica.objects.create(
                    id_animal=animal_obj,
                    numero_historia=animal_data.get('numeroHistoriaClinica', '') or f"HC-{animal_obj.id_animal}",
                    fecha_ultima_desparasitacion=parse_date(anamnesicos_data.get('ultimaDesparasitacion', '')),
                    producto_desparasitacion=anamnesicos_data.get('productoDesparasitacion', ''),
                    vacunas=anamnesicos_data.get('vacunas', ''),
                    enfermedades_anteriores=anamnesicos_data.get('enfermedadesAnteriores', ''),
                    tratamientos_anteriores=anamnesicos_data.get('tratamientos', ''),
                    evolucion_previa=anamnesicos_data.get('evolucion', ''),
                    alimentacion=anamnesicos_data.get('alimentacion', ''),
                    estado_reproductivo=anamnesicos_data.get('historiaReproductiva', ''),
                    fecha_ultimo_celo=parse_date(anamnesicos_data.get('ultimoCelo', '')),
                    fecha_ultimo_parto=parse_date(anamnesicos_data.get('fechaUltimoParto', ''))
                )

            # 5. Consulta
            def parse_float(val):
                if not val: return None
                try:
                    return float(val)
                except:
                    return None
                    
            def parse_int(val):
                if not val: return None
                try:
                    return int(val)
                except:
                    return None

            request = self.context.get('request')
            veterinario_nombre = ""
            if request and hasattr(request, 'user') and request.user.is_authenticated:
                veterinario_nombre = getattr(request.user, 'nombre', '') or getattr(request.user, 'username', '')

            funcionario_cba = hoja3_data.get('funcionarioRecibeCBA') or veterinario_nombre

            if veterinario_nombre:
                if not isinstance(organos_data, dict):
                    organos_data = {}
                organos_data['_veterinario_responsable'] = veterinario_nombre

            consulta = Consulta.objects.create(
                id_historia=historia,
                motivo_consulta=validated_data.get('motivoConsulta', ''),
                frecuencia_respiratoria=parse_int(examen_data.get('frecuenciaRespiratoria')),
                frecuencia_cardiaca=parse_int(examen_data.get('frecuenciaCardiaca')),
                temperatura=parse_float(examen_data.get('temperatura')),
                pulso=examen_data.get('pulso', ''),
                tllc=examen_data.get('tiempoLlenadoCapilar', ''),
                ganglios_linfaticos=examen_data.get('gangliosLinfaticos', ''),
                mucosas=examen_data.get('mucosas', ''),
                actitud_temperamento=examen_data.get('actitudTemperamento', ''),
                evaluacion_sistemas=organos_data,
                
                hallazgos_examen=hoja2_data.get('descripcionHallazgos', ''),
                materiales_utilizados=hoja2_data.get('materialesUtilizados', ''),
                plan_procedimientos=hoja2_data.get('planProcedimientos', ''),
                pronostico=hoja2_data.get('pronostico', ''),
                examenes_complementarios=hoja2_data.get('examenes', {}),
                
                ingresa_cba=True if hoja3_data.get('ingresaCBA') == 'SI' else False,
                motivo_ingreso_cba=hoja3_data.get('motivoIngresoNoIngreso', ''),
                canil_asignado=hoja3_data.get('canilAsignado', ''),
                funcionario_recibe_cba=funcionario_cba
            )

            # 6. Diagnóstico y Catálogo de Patologías
            problemas = hoja2_data.get('listaProblemas', [])
            diag_final = hoja2_data.get('diagnosticoFinal', '')
            if problemas or diag_final:
                diag_obj = Diagnostico.objects.create(
                    id_consulta=consulta,
                    lista_problemas="; ".join(problemas) if isinstance(problemas, list) else str(problemas),
                    descripcion=diag_final,
                    tipo_diagnostico='Final'
                )

                id_patologia = hoja2_data.get('idPatologia')
                patologia_obj = None
                from patologias.models import Patologia
                if id_patologia:
                    patologia_obj = Patologia.objects.filter(pk=id_patologia).first()
                elif diag_final:
                    patologia_obj = Patologia.objects.filter(nombre__iexact=str(diag_final).strip()).first()

                if patologia_obj:
                    from clinica.models import DiagnosticoPatologia
                    DiagnosticoPatologia.objects.create(
                        id_diagnostico=diag_obj,
                        id_patologia=patologia_obj,
                        hallazgos_pruebas=hoja2_data.get('descripcionHallazgos', '')
                    )

            # 7. Tratamientos
            tratamientos_list = hoja2_data.get('tratamientos', [])
            medicamentos_descontados = []

            def _nuevo_tratamiento(t):
                return Tratamientos(
                    id_consulta=consulta,
                    producto_base=t.get('productoBase', ''),
                    dosis_basica=t.get('dosisBasica', ''),
                    presentacion=t.get('presentacion', ''),
                    via_administracion=t.get('via', ''),
                    frecuencia_duracion=t.get('frecuenciaDuracion', '')
                )

            vinculados = [t for t in tratamientos_list if isinstance(t, dict) and t.get('id_medicamento')]

            if tratamientos_list and not vinculados:
                # Flujo original: tratamientos solo texto, no se toca inventario
                Tratamientos.objects.bulk_create([_nuevo_tratamiento(t) for t in tratamientos_list])

            elif tratamientos_list:
                from django.utils import timezone
                from medicamentos.models import TratamientoMedicamentos
                from inventario.models import Salidas

                ahora = timezone.now()
                if timezone.is_aware(ahora):
                    ahora = timezone.localtime(ahora)

                responsable = ""
                user = getattr(request, 'user', None) if request else None
                if user is not None and getattr(user, 'is_authenticated', False):
                    responsable = " ".join(
                        p for p in [getattr(user, 'nombre', ''), getattr(user, 'apellido', '')] if p
                    ).strip() or veterinario_nombre

                for t in tratamientos_list:
                    tratamiento = _nuevo_tratamiento(t)
                    tratamiento.save()

                    id_med = t.get('id_medicamento')
                    if not id_med:
                        continue

                    descontar = _debe_descontar(t)
                    TratamientoMedicamentos.objects.create(
                        id_tratamiento=tratamiento,
                        id_medicamento_id=id_med,
                        dosis=t.get('dosisBasica', ''),
                        via=t.get('via', ''),
                        fecha_aplicacion=ahora.date() if descontar else None,
                        hora_administrada=ahora.time() if descontar else None,
                        responsable_admin=responsable or None,
                    )

                    if descontar:
                        cantidad = t['_cantidad_usada']
                        ok, disponible = _descontar_fifo(id_med, cantidad)
                        if not ok:
                            # Puede ocurrir si otro usuario consumió stock entre la validación y el guardado.
                            # Al lanzar la excepción la transacción completa se revierte.
                            raise serializers.ValidationError({
                                "tratamientos": [
                                    f"Stock insuficiente para el medicamento {id_med}. "
                                    f"Disponibles: {disponible}, solicitados: {cantidad}."
                                ]
                            })
                        medicamentos_descontados.append({
                            "id_medicamento": id_med,
                            "cantidad": cantidad,
                            "estado": "Descontado correctamente"
                        })

                if medicamentos_descontados:
                    Salidas.objects.create(
                        motivo=f"Uso en Consulta #{consulta.id_consulta} - Animal #{animal_obj.id_animal}"[:100]
                    )

        return {
            'status': 'success',
            'id_animal': animal_obj.id_animal,
            'historia_id': historia.id_historia,
            'consulta_id': consulta.id_consulta,
            'numero_historia': historia.numero_historia or f"HC-{animal_obj.id_animal}",
            'medicamentos_descontados': medicamentos_descontados
        }
