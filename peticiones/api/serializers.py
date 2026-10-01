from rest_framework import serializers
from django.db import transaction
from peticiones.models import (
    TipoPeticion, Peticiones, Ubicaciones,
    SeguimientoPeticionesVisita, VisitaAnimal,
    SeguimientoVisitaFuncionarios,
)
from animales.models import Animal, Responsables


class TiposPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoPeticion
        fields = ['id_tipo', 'nombre', 'descripcion']


class IniciarPeticionSerializer(serializers.ModelSerializer):
    # Campos de ubicación que vienen en el mismo POST
    direccion = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    latitud = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    longitud = serializers.DecimalField(max_digits=10, decimal_places=7, required=False, allow_null=True)
    foto = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'numero_radicado',
            'id_tipo',
            'descripcion',
            'prioridad',
            'foto',
            'direccion',
            'latitud',
            'longitud',
        ]
        read_only_fields = ['id_peticion', 'numero_radicado']

    @transaction.atomic
    def create(self, validated_data):
        direccion = validated_data.pop('direccion', None)
        latitud = validated_data.pop('latitud', None)
        longitud = validated_data.pop('longitud', None)

        # 1. Si viene algún dato de mapa/dirección, creamos el registro en Ubicaciones
        ubicacion = None
        if direccion or latitud is not None or longitud is not None:
            ubicacion = Ubicaciones.objects.create(
                direccion=direccion,
                latitud=latitud,
                longitud=longitud
            )

        # 2. Creamos la petición enlazando la ubicación creada
        peticion = Peticiones.objects.create(
            id_ubicacion=ubicacion,
            **validated_data
        )
        
        # 3. Generar radicado único basado en el ID recién creado
        # Se rellena con ceros a la izquierda (ej. PET-2026-00055)
        peticion.numero_radicado = f"PET-2026-{str(peticion.id_peticion).zfill(5)}"
        peticion.save(update_fields=['numero_radicado'])
        
        return peticion


class ListarPeticionesSerializer(serializers.ModelSerializer):
    tipo = serializers.CharField(source='id_tipo.nombre', read_only=True)
    estado = serializers.CharField(source='id_estado.nombre', read_only=True)
    asignado_a_nombre = serializers.CharField(source='asignado_a.nombre', read_only=True)
    asignado_a_apellido = serializers.CharField(source='asignado_a.apellido', read_only=True)
    ubicacion_direccion = serializers.CharField(source='id_ubicacion.direccion', read_only=True)
    ubicacion_latitud = serializers.DecimalField(source='id_ubicacion.latitud', max_digits=10, decimal_places=7, read_only=True)
    ubicacion_longitud = serializers.DecimalField(source='id_ubicacion.longitud', max_digits=10, decimal_places=7, read_only=True)

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'numero_radicado',
            'tipo',
            'estado',
            'descripcion',
            'prioridad',
            'fecha',
            'fecha_asignacion',
            'asignado_a_nombre',
            'asignado_a_apellido',
            'ubicacion_direccion',
            'ubicacion_latitud',
            'ubicacion_longitud',
            'foto'
        ]

class AsignarPeticionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Peticiones
        fields = ['asignado_a', 'asignado_por', 'fecha_asignacion', 'id_estado']
        read_only_fields = ['asignado_por', 'fecha_asignacion']


class DetallePeticionSerializer(serializers.ModelSerializer):
    codigo = serializers.SerializerMethodField()
    estado = serializers.CharField(source='id_estado.nombre', default='Pendiente')
    tipoEstado = serializers.CharField(source='id_estado.nombre', default='Pendiente')
    solicitanteNombre = serializers.SerializerMethodField()
    solicitanteTelefono = serializers.CharField(source='responsable.telefono', default='')
    solicitanteDireccion = serializers.CharField(source='id_ubicacion.direccion', default='')
    solicitanteComuna = serializers.SerializerMethodField()
    especie = serializers.CharField(source='id_tipo.nombre', default='')
    motivo = serializers.CharField(source='descripcion', default='')
    fechaAsignada = serializers.SerializerMethodField()
    observaciones = serializers.CharField(source='descripcion', default='')

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'codigo',
            'estado',
            'tipoEstado',
            'solicitanteNombre',
            'solicitanteTelefono',
            'solicitanteDireccion',
            'solicitanteComuna',
            'especie',
            'motivo',
            'fechaAsignada',
            'observaciones'
        ]

    def get_codigo(self, obj):
        return obj.numero_radicado or f"#INC-2026-{obj.id_peticion:06d}"

    def get_solicitanteNombre(self, obj):
        if obj.responsable:
            return f"{obj.responsable.nombre or ''} {obj.responsable.apellido or ''}".strip() or "Anónimo"
        return "Anónimo"

    def get_solicitanteComuna(self, obj):
        return ""

    def get_fechaAsignada(self, obj):
        fecha = obj.fecha_asignacion or obj.fecha
        if fecha:
            return fecha.strftime("%d %b %Y, %H:%M")
        return ""



# ============================================================
# SERIALIZERS DEL ACTA DE VISITA (seguimiento_peticiones_visita)
# ============================================================

class UbicacionEditableSerializer(serializers.ModelSerializer):
    """
    Permite crear o actualizar una ubicación del lugar de atención.
    Se usa dentro del acta de visita (puede diferir del lugar reportado por el ciudadano).
    """
    class Meta:
        model = Ubicaciones
        fields = ['id_ubicacion', 'direccion', 'latitud', 'longitud']
        read_only_fields = ['id_ubicacion']


class SeguimientoVisitaFuncionariosSerializer(serializers.ModelSerializer):
    """
    Funcionario que participó en la visita.
    - Si es usuario del sistema: id_usuario + nombre/cargo opcionales (se completan del user).
    - Si es externo/pasante: es_externo=True + nombre + cargo + institucion + documento_identidad.
    """
    class Meta:
        model = SeguimientoVisitaFuncionarios
        fields = [
            'id', 'id_usuario', 'nombre', 'cargo',
            'es_externo', 'es_principal',
            'institucion', 'documento_identidad',
        ]
        read_only_fields = ['id']

    def validate(self, data):
        es_externo = data.get('es_externo', False)
        id_usuario = data.get('id_usuario', None)
        nombre = data.get('nombre', '').strip()
        if not es_externo and not id_usuario and not nombre:
            raise serializers.ValidationError(
                "Debe proveer id_usuario (usuario del sistema) o nombre (si es externo/pasante)."
            )
        return data


class AnimalRapidoSerializer(serializers.Serializer):
    """
    Datos para registrar un animal desde el acta de visita.
    id_raza es opcional; si no se envía, se busca una raza genérica 'Mestizo/Desconocida'.
    """
    id_animal = serializers.IntegerField(required=False, allow_null=True,
                                         help_text="ID de animal ya existente en el sistema.")
    nombre = serializers.CharField(max_length=100, required=False, default='Sin nombre')
    id_raza = serializers.IntegerField(required=False, allow_null=True)
    sexo = serializers.CharField(max_length=10, required=False, allow_blank=True, allow_null=True)
    color = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)
    peso = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    esterilizado = serializers.BooleanField(required=False, default=False)
    caracteristicas = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    fecha_nacimiento = serializers.DateField(required=False, allow_null=True)

    def get_or_create_animal(self, data):
        """
        Retorna un Animal existente (si se envió id_animal)
        o crea uno nuevo con los datos provistos.
        """
        from especies.models import Raza
        from datetime import date

        if data.get('id_animal'):
            try:
                return Animal.objects.get(pk=data['id_animal'])
            except Animal.DoesNotExist:
                raise serializers.ValidationError(
                    f"No existe un animal con id_animal={data['id_animal']}."
                )

        # Determinar raza
        id_raza = data.get('id_raza')
        if id_raza:
            try:
                raza = Raza.objects.get(pk=id_raza)
            except Raza.DoesNotExist:
                raise serializers.ValidationError(
                    f"No existe una raza con id_raza={id_raza}."
                )
        else:
            # Raza por defecto: Mestizo o la primera disponible
            raza = (
                Raza.objects.filter(nombre__icontains='mestizo').first()
                or Raza.objects.filter(nombre__icontains='desconocido').first()
                or Raza.objects.filter(activo=True).first()
                or Raza.objects.first()
            )
            if not raza:
                raise serializers.ValidationError(
                    "No hay razas configuradas en el sistema. Contacta al administrador."
                )

        animal = Animal.objects.create(
            id_raza=raza,
            nombre=data.get('nombre') or 'Sin nombre',
            sexo=data.get('sexo'),
            color=data.get('color'),
            peso=data.get('peso'),
            esterilizado=data.get('esterilizado', False),
            caracteristicas=data.get('caracteristicas'),
            fecha_nacimiento=data.get('fecha_nacimiento'),
            fecha_ingreso=date.today(),
            activo=True,
        )
        return animal


class SeguimientoPeticionesVisitaSerializer(serializers.ModelSerializer):
    """
    Serializer principal del acta de visita de campo.

    Soporta 3 modos de animales:
    A) Un animal existente o nuevo (campos directos: nombre_paciente, paciente_especie, etc.)
    B) Varios animales detallados: campo 'animales' → lista de AnimalRapidoSerializer
    C) Registro rápido en lote: solo nro_animales_atendidos + descripcion_paciente (sin lista)

    Funcionarios:
    - 'funcionarios': lista de SeguimientoVisitaFuncionariosSerializer
      Incluye usuarios del sistema (id_usuario) y/o externos (es_externo=True + datos).

    Lugar de atención:
    - 'lugar_atencion': objeto con direccion/latitud/longitud → crea/actualiza una Ubicacion.
    """
    # Lugar de atención editable
    lugar_atencion = UbicacionEditableSerializer(required=False, allow_null=True, write_only=True)
    lugar_atencion_detalle = UbicacionEditableSerializer(
        source='id_ubicacion_visita', read_only=True
    )

    # Funcionarios (varios)
    funcionarios = SeguimientoVisitaFuncionariosSerializer(many=True, required=False, write_only=True)
    funcionarios_detalle = SeguimientoVisitaFuncionariosSerializer(
        many=True, read_only=True, source='funcionarios'
    )

    # Animales (modo B: varios detallados)
    animales = AnimalRapidoSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = SeguimientoPeticionesVisita
        fields = [
            # Identificadores
            'id_seguimiento', 'id_peticion', 'id_veterinario',
            'numero_radicado', 'fecha_atencion',
            # Propietario / tutor / responsable
            'propietario_nombre', 'propietario_cedula',
            'propietario_telefono', 'propietario_email',
            'propietario_barrio', 'propietario_direccion',
            # Quien reporta
            'quien_reporta', 'quien_reporta_otro',
            'solicitud_atencion_por',
            # Lugar de atención
            'lugar_atencion', 'lugar_atencion_detalle', 'id_ubicacion_visita',
            # Datos del paciente (modo A: un animal / modo C: lote rápido)
            'id_animal',
            'nro_animales_atendidos',
            'nombre_paciente', 'paciente_especie', 'paciente_sexo',
            'paciente_color', 'paciente_raza', 'paciente_edad',
            'peso_paciente', 'esterilizacion_paciente',
            'descripcion_paciente', 'desparasitacion',
            # Modo B: varios animales detallados
            'animales',
            # Anamnesis
            'anamnesis_descripcion_queja',
            # Atención / tratamiento
            'tratamiento_realizado',
            # Pruebas complementarias
            'pruebas_complementarias', 'resultado_pruebas',
            # Compromisos y fundamento legal
            'compromisos', 'fundamento_legal', 'plazo_dias_cumplimiento',
            # Funcionarios (write y read)
            'funcionarios', 'funcionarios_detalle',
            # Notificación
            'notificado_nombre', 'notificaciones_identificacion',
            'fecha_notificacion',
            # Notificador
            'notificador_nombre', 'notificador_identificacion', 'notificador_cargo',
            # Firmas
            'firma_notificador', 'firma_notificado',
            # Observaciones generales
            'observacion',
            # Fecha de registro automática
            'fecha',
        ]
        read_only_fields = ['id_seguimiento', 'fecha', 'lugar_atencion_detalle', 'funcionarios_detalle']

    @transaction.atomic
    def create(self, validated_data):
        lugar_atencion_data = validated_data.pop('lugar_atencion', None)
        funcionarios_data = validated_data.pop('funcionarios', [])
        animales_data = validated_data.pop('animales', [])

        # 1. Crear/actualizar ubicación del lugar de atención
        if lugar_atencion_data:
            ubicacion_visita = Ubicaciones.objects.create(**lugar_atencion_data)
            validated_data['id_ubicacion_visita'] = ubicacion_visita

        # 2. Crear el registro principal de seguimiento
        seguimiento = SeguimientoPeticionesVisita.objects.create(**validated_data)

        # 3. Procesar animales según el modo recibido
        if animales_data:
            # Modo B: Lista detallada de animales
            animal_serializer = AnimalRapidoSerializer()
            primer_animal = None
            for animal_data in animales_data:
                animal = animal_serializer.get_or_create_animal(animal_data)
                if primer_animal is None:
                    primer_animal = animal
                VisitaAnimal.objects.create(
                    id_animal=animal,
                    id_seguimiento=seguimiento,
                )
            # El primer animal va como referencia principal en el acta
            if primer_animal and not seguimiento.id_animal_id:
                SeguimientoPeticionesVisita.objects.filter(
                    pk=seguimiento.pk
                ).update(id_animal_id=primer_animal.pk)
                seguimiento.id_animal_id = primer_animal.pk

            # Actualizar el contador si no vino explícito
            if not seguimiento.nro_animales_atendidos:
                SeguimientoPeticionesVisita.objects.filter(
                    pk=seguimiento.pk
                ).update(nro_animales_atendidos=len(animales_data))

        elif seguimiento.id_animal_id:
            # Modo A: un solo animal ya existente o recién vinculado
            VisitaAnimal.objects.create(
                id_animal_id=seguimiento.id_animal_id,
                id_seguimiento=seguimiento,
            )

        # 4. Crear los funcionarios de la visita
        for func_data in funcionarios_data:
            id_usuario_obj = func_data.pop('id_usuario', None)
            nombre = func_data.get('nombre', '')
            cargo = func_data.get('cargo', '')

            # Si es usuario del sistema, completar nombre/cargo desde el perfil
            if id_usuario_obj and not nombre:
                func_data['nombre'] = f"{id_usuario_obj.nombre} {id_usuario_obj.apellido}".strip()
            if id_usuario_obj and not cargo:
                func_data['cargo'] = id_usuario_obj.id_rol.nombre_rol if id_usuario_obj.id_rol else ''

            SeguimientoVisitaFuncionarios.objects.create(
                id_seguimiento=seguimiento,
                id_usuario=id_usuario_obj,
                **func_data,
            )

        return seguimiento

    @transaction.atomic
    def update(self, instance, validated_data):
        lugar_atencion_data = validated_data.pop('lugar_atencion', None)
        funcionarios_data = validated_data.pop('funcionarios', None)
        validated_data.pop('animales', None)  # No se reemplaza la lista de animales por PATCH

        # Actualizar o crear ubicación de la visita
        if lugar_atencion_data:
            if instance.id_ubicacion_visita:
                for attr, value in lugar_atencion_data.items():
                    setattr(instance.id_ubicacion_visita, attr, value)
                instance.id_ubicacion_visita.save()
            else:
                ubicacion_visita = Ubicaciones.objects.create(**lugar_atencion_data)
                instance.id_ubicacion_visita = ubicacion_visita

        # Actualizar campos directos del seguimiento
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Reemplazar funcionarios solo si se envían explícitamente
        if funcionarios_data is not None:
            instance.funcionarios.all().delete()
            for func_data in funcionarios_data:
                id_usuario_obj = func_data.pop('id_usuario', None)
                nombre = func_data.get('nombre', '')
                cargo = func_data.get('cargo', '')
                if id_usuario_obj and not nombre:
                    func_data['nombre'] = f"{id_usuario_obj.nombre} {id_usuario_obj.apellido}".strip()
                if id_usuario_obj and not cargo:
                    func_data['cargo'] = id_usuario_obj.id_rol.nombre_rol if id_usuario_obj.id_rol else ''
                SeguimientoVisitaFuncionarios.objects.create(
                    id_seguimiento=instance,
                    id_usuario=id_usuario_obj,
                    **func_data,
                )

        return instance

