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

            # 4. Historia Clínica
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
                funcionario_recibe_cba=hoja3_data.get('funcionarioRecibeCBA', '')
            )

            # 6. Diagnóstico
            problemas = hoja2_data.get('listaProblemas', [])
            diag_final = hoja2_data.get('diagnosticoFinal', '')
            if problemas or diag_final:
                Diagnostico.objects.create(
                    id_consulta=consulta,
                    lista_problemas="; ".join(problemas) if isinstance(problemas, list) else str(problemas),
                    descripcion=diag_final,
                    tipo_diagnostico='Final'
                )

            # 7. Tratamientos
            tratamientos_list = hoja2_data.get('tratamientos', [])
            if tratamientos_list:
                Tratamientos.objects.bulk_create([
                    Tratamientos(
                        id_consulta=consulta,
                        producto_base=t.get('productoBase', ''),
                        dosis_basica=t.get('dosisBasica', ''),
                        presentacion=t.get('presentacion', ''),
                        via_administracion=t.get('via', ''),
                        frecuencia_duracion=t.get('frecuenciaDuracion', '')
                    ) for t in tratamientos_list
                ])

        return {
            'status': 'success',
            'id_animal': animal_obj.id_animal,
            'historia_id': historia.id_historia,
            'consulta_id': consulta.id_consulta,
            'numero_historia': historia.numero_historia or ''
        }
