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

            # 1. Especie y Raza
            especie_nombre = animal_data.get('especie', 'Desconocida')
            if especie_nombre == 'Otro':
                especie_nombre = animal_data.get('otraEspecie', 'Otra')
            especie_obj, _ = Especie.objects.get_or_create(nombre=especie_nombre)
            
            raza_nombre = animal_data.get('raza', 'Mestizo')
            if not raza_nombre:
                raza_nombre = 'Mestizo'
            raza_obj, _ = Raza.objects.get_or_create(nombre=raza_nombre, defaults={'id_especie': especie_obj})

            # 2. Animal
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

            # 3. Responsable
            doc_identidad = tenedor_data.get('documentoIdentidad', '')
            if doc_identidad:
                responsable_obj, created = Responsables.objects.get_or_create(
                    documento=doc_identidad,
                    defaults={
                        'nombre': tenedor_data.get('nombres', 'Sin Nombre'),
                        'telefono': tenedor_data.get('telefono', ''),
                        'correo': tenedor_data.get('correoElectronico', ''),
                        'direccion': tenedor_data.get('direccion', ''),
                    }
                )
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

            historia = HistoriaClinica.objects.create(
                id_animal=animal_obj,
                numero_historia=animal_data.get('numeroHistoriaClinica', ''),
                fecha_ultima_desparasitacion=parse_date(anamnesicos_data.get('ultimaDesparasitacion', '')),
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

        return {'status': 'success', 'historia_id': historia.id_historia, 'consulta_id': consulta.id_consulta}
