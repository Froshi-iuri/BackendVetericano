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
