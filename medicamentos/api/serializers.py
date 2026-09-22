from rest_framework import serializers
from medicamentos.models import (
    Medicamento,
    Medicamentos,
    AdministracionMedicamento,
    TratamientoMedicamentos,
)


class MedicamentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medicamento
        fields = '__all__'


class MedicamentosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medicamentos
        fields = '__all__'


class AdministracionMedicamentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdministracionMedicamento
        fields = '__all__'


class TratamientoMedicamentosSerializer(serializers.ModelSerializer):
    class Meta:
        model = TratamientoMedicamentos
        fields = '__all__'
