from rest_framework import serializers
from django.db import transaction
from peticiones.models import (
    TipoPeticion, Peticiones, Ubicaciones,
    SeguimientoPeticionesVisita, VisitaAnimal,
    SeguimientoVisitaFuncionarios, EvidenciaPeticiones,
)
from peticiones.utils import subir_o_asegurar_cloudinary, obtener_fotos_acta_con_fallback
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
    fotos = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True
    )

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'numero_radicado',
            'id_tipo',
            'descripcion',
            'prioridad',
            'foto',
            'fotos',
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
        fotos_input = validated_data.pop('fotos', [])
        foto_input = validated_data.get('foto', None)

        # 1. Si viene algún dato de mapa/dirección, creamos el registro en Ubicaciones
        ubicacion = None
        if direccion or latitud is not None or longitud is not None:
            ubicacion = Ubicaciones.objects.create(
                direccion=direccion,
                latitud=latitud,
                longitud=longitud
            )

        # Consolidar lista de fotos a procesar
        lista_fotos = []
        if fotos_input:
            lista_fotos.extend(fotos_input)
        if foto_input and foto_input not in lista_fotos:
            lista_fotos.insert(0, foto_input)

        # Procesar y subir a Cloudinary si es necesario
        urls_procesadas = []
        for f in lista_fotos:
            url_cloud = subir_o_asegurar_cloudinary(f)
            if url_cloud and url_cloud not in urls_procesadas:
                urls_procesadas.append(url_cloud)

        if urls_procesadas:
            validated_data['foto'] = urls_procesadas[0]
        elif foto_input:
            url_simple = subir_o_asegurar_cloudinary(foto_input)
            if url_simple:
                validated_data['foto'] = url_simple

        # 2. Creamos la petición enlazando la ubicación creada
        peticion = Peticiones.objects.create(
            id_ubicacion=ubicacion,
            **validated_data
        )
        
        # 3. Generar radicado único basado en el ID recién creado
        # Se rellena con ceros a la izquierda (ej. PET-2026-00055)
        peticion.numero_radicado = f"PET-2026-{str(peticion.id_peticion).zfill(5)}"
        peticion.save(update_fields=['numero_radicado'])

        # 4. Registrar todas las fotos en EvidenciaPeticiones
        for idx, url in enumerate(urls_procesadas, start=1):
            EvidenciaPeticiones.objects.create(
                id_peticion=peticion,
                ruta_archivo=url,
                descripcion=f"Evidencia #{idx} de la petición"
            )
        
        return peticion


class ListarPeticionesSerializer(serializers.ModelSerializer):
    tipo = serializers.CharField(source='id_tipo.nombre', read_only=True)
    estado = serializers.CharField(source='id_estado.nombre', read_only=True)
    asignado_a_nombre = serializers.CharField(source='asignado_a.nombre', read_only=True)
    asignado_a_apellido = serializers.CharField(source='asignado_a.apellido', read_only=True)
    ubicacion_direccion = serializers.CharField(source='id_ubicacion.direccion', read_only=True)
    ubicacion_latitud = serializers.DecimalField(source='id_ubicacion.latitud', max_digits=10, decimal_places=7, read_only=True)
    ubicacion_longitud = serializers.DecimalField(source='id_ubicacion.longitud', max_digits=10, decimal_places=7, read_only=True)
    fotos = serializers.SerializerMethodField()
    total_fotos = serializers.SerializerMethodField()

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
            'foto',
            'fotos',
            'total_fotos',
        ]

    def _get_fotos_list(self, obj):
        if hasattr(obj, '_fotos_cache'):
            return obj._fotos_cache
        evidencias_cache = getattr(obj, '_prefetched_objects_cache', {}).get('evidenciapeticiones_set')
        if evidencias_cache is not None:
            urls = [e.ruta_archivo for e in evidencias_cache if e.ruta_archivo]
        else:
            urls = list(
                EvidenciaPeticiones.objects.filter(id_peticion=obj)
                .exclude(ruta_archivo__isnull=True)
                .exclude(ruta_archivo='')
                .values_list('ruta_archivo', flat=True)
            )
        if not urls and obj.foto:
            urls = [obj.foto]
        obj._fotos_cache = urls
        return urls

    def get_fotos(self, obj):
        return self._get_fotos_list(obj)

    def get_total_fotos(self, obj):
        return len(self._get_fotos_list(obj))

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
    fotos = serializers.SerializerMethodField()
    total_fotos = serializers.SerializerMethodField()

    # Compatibilidad snake_case con PeticionListResponse y clientes móviles
    numero_radicado = serializers.CharField(read_only=True)
    tipo = serializers.CharField(source='id_tipo.nombre', default='', read_only=True)
    asignado_a_nombre = serializers.CharField(source='asignado_a.nombre', default='', read_only=True)
    asignado_a_apellido = serializers.CharField(source='asignado_a.apellido', default='', read_only=True)
    ubicacion_direccion = serializers.CharField(source='id_ubicacion.direccion', default='', read_only=True)
    ubicacion_latitud = serializers.DecimalField(source='id_ubicacion.latitud', max_digits=10, decimal_places=7, read_only=True, allow_null=True)
    ubicacion_longitud = serializers.DecimalField(source='id_ubicacion.longitud', max_digits=10, decimal_places=7, read_only=True, allow_null=True)

    class Meta:
        model = Peticiones
        fields = [
            'id_peticion',
            'codigo',
            'numero_radicado',
            'estado',
            'tipoEstado',
            'tipo',
            'especie',
            'motivo',
            'descripcion',
            'prioridad',
            'fecha',
            'fecha_asignacion',
            'fechaAsignada',
            'solicitanteNombre',
            'solicitanteTelefono',
            'solicitanteDireccion',
            'solicitanteComuna',
            'asignado_a_nombre',
            'asignado_a_apellido',
            'ubicacion_direccion',
            'ubicacion_latitud',
            'ubicacion_longitud',
            'observaciones',
            'foto',
            'fotos',
            'total_fotos',
        ]

    def _get_fotos_list(self, obj):
        if hasattr(obj, '_fotos_cache'):
            return obj._fotos_cache
        evidencias_cache = getattr(obj, '_prefetched_objects_cache', {}).get('evidenciapeticiones_set')
        if evidencias_cache is not None:
            urls = [e.ruta_archivo for e in evidencias_cache if e.ruta_archivo]
        else:
            urls = list(
                EvidenciaPeticiones.objects.filter(id_peticion=obj)
                .exclude(ruta_archivo__isnull=True)
                .exclude(ruta_archivo='')
                .values_list('ruta_archivo', flat=True)
            )
        if not urls and obj.foto:
            urls = [obj.foto]
        obj._fotos_cache = urls
        return urls

    def get_fotos(self, obj):
        return self._get_fotos_list(obj)

    def get_total_fotos(self, obj):
        return len(self._get_fotos_list(obj))

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

    # Fotos del acta de visita (una o varias, subidas a Cloudinary)
    foto = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    fotos = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True
    )

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
            'nombre_funcionario', 'funcionario_cargo',
            'funcionarios', 'funcionarios_detalle',
            # Notificación
            'notificado_nombre', 'notificaciones_identificacion',
            'fecha_notificacion',
            # Notificador
            'notificador_nombre', 'notificador_identificacion', 'notificador_cargo',
            # Firmas
            'firma_notificador', 'firma_notificado',
            # Fotos del acta
            'foto', 'fotos',
            # Observaciones generales
            'observacion',
            # Fecha de registro automática
            'fecha',
        ]
        read_only_fields = ['id_seguimiento', 'fecha', 'lugar_atencion_detalle', 'funcionarios_detalle']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Fallback de descripcion_paciente si está vacío pero el animal tiene características
        if not data.get('descripcion_paciente'):
            animal = getattr(instance, 'id_animal', None)
            if animal and getattr(animal, 'caracteristicas', None):
                data['descripcion_paciente'] = animal.caracteristicas

        # Incluir lista de animales atendidos para modo B y detalle completo
        if 'animales' not in data or not data['animales']:
            from peticiones.models import VisitaAnimal
            visitas = (
                VisitaAnimal.objects
                .filter(id_seguimiento_id=instance.pk)
                .select_related('id_animal')
                .order_by('id_visita')
            )
            data['animales'] = VisitaAnimalListSerializer(visitas, many=True).data

        fotos = obtener_fotos_acta_con_fallback(instance)
        data['fotos'] = fotos
        data['foto'] = fotos[0] if fotos else None
        data['total_fotos'] = len(fotos)
        return data

    @transaction.atomic
    def create(self, validated_data):
        lugar_atencion_data = validated_data.pop('lugar_atencion', None)
        funcionarios_data = validated_data.pop('funcionarios', [])
        animales_data = validated_data.pop('animales', [])
        fotos_input = validated_data.pop('fotos', [])
        foto_input = validated_data.pop('foto', None)

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

        # 5. Guardar fotos de la visita (Cloudinary + EvidenciaPeticiones)
        lista_fotos = []
        if fotos_input:
            lista_fotos.extend(fotos_input)
        if foto_input and foto_input not in lista_fotos:
            lista_fotos.insert(0, foto_input)

        for idx, f_item in enumerate(lista_fotos, start=1):
            url_cloud = subir_o_asegurar_cloudinary(f_item)
            if url_cloud:
                EvidenciaPeticiones.objects.create(
                    id_peticion=seguimiento.id_peticion,
                    ruta_archivo=url_cloud,
                    descripcion=f"Acta de visita #{seguimiento.id_seguimiento} - Foto #{idx}"
                )

        return seguimiento

    @transaction.atomic
    def update(self, instance, validated_data):
        lugar_atencion_data = validated_data.pop('lugar_atencion', None)
        funcionarios_data = validated_data.pop('funcionarios', None)
        fotos_input = validated_data.pop('fotos', None)
        foto_input = validated_data.pop('foto', None)
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

        # Registrar fotos adicionales si se envían
        if fotos_input is not None or foto_input is not None:
            lista_fotos = []
            if fotos_input:
                lista_fotos.extend(fotos_input)
            if foto_input and foto_input not in lista_fotos:
                lista_fotos.insert(0, foto_input)

            if lista_fotos:
                conteo_previo = EvidenciaPeticiones.objects.filter(
                    id_peticion=instance.id_peticion,
                    descripcion__startswith=f"Acta de visita #{instance.id_seguimiento}"
                ).count()
                for idx, f_item in enumerate(lista_fotos, start=conteo_previo + 1):
                    url_cloud = subir_o_asegurar_cloudinary(f_item)
                    if url_cloud:
                        EvidenciaPeticiones.objects.create(
                            id_peticion=instance.id_peticion,
                            ruta_archivo=url_cloud,
                            descripcion=f"Acta de visita #{instance.id_seguimiento} - Foto #{idx}"
                        )

        return instance


# ============================================================
# SERIALIZER LIVIANO DE SOLO LECTURA PARA LISTAR SEGUIMIENTOS
# ============================================================

class VisitaAnimalListSerializer(serializers.ModelSerializer):
    """Representación compacta de cada animal atendido en una visita."""
    id_animal = serializers.IntegerField(source='id_animal.id_animal', read_only=True)
    nombre = serializers.CharField(source='id_animal.nombre', read_only=True, default='', allow_null=True)
    sexo = serializers.CharField(source='id_animal.sexo', read_only=True, default='', allow_null=True)
    color = serializers.CharField(source='id_animal.color', read_only=True, default='', allow_null=True)
    peso = serializers.DecimalField(
        source='id_animal.peso', max_digits=5, decimal_places=2,
        read_only=True, allow_null=True,
    )
    caracteristicas = serializers.CharField(
        source='id_animal.caracteristicas', read_only=True, default='', allow_null=True
    )
    descripcion_paciente = serializers.CharField(
        source='id_animal.caracteristicas', read_only=True, default='', allow_null=True
    )

    class Meta:
        model = VisitaAnimal
        fields = [
            'id_visita',
            'id_animal', 'nombre', 'sexo', 'color', 'peso',
            'caracteristicas', 'descripcion_paciente',
            'fecha', 'nro_radicado_atencion',
            'quien_reporta', 'lugar_atencion_direccion',
            'nombre_notificado', 'documento_notificado', 'fecha_notificacion',
        ]


class ListarSeguimientoPeticionesVisitaSerializer(serializers.ModelSerializer):
    """
    Serializer de solo lectura para LISTAR actas de visita.
    Liviano: no acepta nested writes, solo expone contadores y
    resúmenes para tablas/pantallas de consulta.
    """
    peticion_numero_radicado = serializers.CharField(
        source='id_peticion.numero_radicado', read_only=True, default=''
    )
    peticion_estado = serializers.CharField(
        source='id_peticion.id_estado.nombre', read_only=True, default=''
    )
    veterinario_nombre = serializers.SerializerMethodField()
    veterinario_email = serializers.CharField(
        source='id_veterinario.email', read_only=True, default=''
    )
    lugar_atencion = serializers.SerializerMethodField()
    funcionarios = SeguimientoVisitaFuncionariosSerializer(many=True, read_only=True)
    total_funcionarios = serializers.SerializerMethodField()
    animales = serializers.SerializerMethodField()
    total_animales = serializers.SerializerMethodField()
    descripcion_paciente = serializers.SerializerMethodField()
    foto = serializers.SerializerMethodField()
    fotos = serializers.SerializerMethodField()
    total_fotos = serializers.SerializerMethodField()

    class Meta:
        model = SeguimientoPeticionesVisita
        fields = [
            'id_seguimiento',
            'numero_radicado',
            'fecha', 'fecha_atencion',
            'id_peticion', 'peticion_numero_radicado', 'peticion_estado',
            'id_veterinario', 'veterinario_nombre', 'veterinario_email',
            'propietario_nombre', 'propietario_cedula',
            'propietario_telefono', 'propietario_email',
            'propietario_barrio', 'propietario_direccion',
            'quien_reporta',
            'nro_animales_atendidos', 'total_animales', 'animales',
            'nombre_paciente', 'paciente_especie', 'paciente_sexo',
            'paciente_raza', 'paciente_color', 'paciente_edad', 'peso_paciente',
            'descripcion_paciente', 'esterilizacion_paciente', 'desparasitacion',
            'anamnesis_descripcion_queja', 'tratamiento_realizado',
            'compromisos', 'plazo_dias_cumplimiento',
            'lugar_atencion',
            'nombre_funcionario', 'funcionario_cargo',
            'funcionarios', 'total_funcionarios',
            'notificado_nombre', 'notificaciones_identificacion', 'fecha_notificacion',
            'notificador_nombre', 'notificador_cargo',
            'foto', 'fotos', 'total_fotos',
            'observacion',
        ]
        read_only_fields = fields

    def get_descripcion_paciente(self, obj):
        if obj.descripcion_paciente:
            return obj.descripcion_paciente
        animal = getattr(obj, 'id_animal', None)
        if animal and getattr(animal, 'caracteristicas', None):
            return animal.caracteristicas
        return ''

    def _get_fotos_list(self, obj):
        if not hasattr(obj, '_fotos_cache'):
            obj._fotos_cache = obtener_fotos_acta_con_fallback(obj)
        return obj._fotos_cache

    def get_fotos(self, obj):
        return self._get_fotos_list(obj)

    def get_foto(self, obj):
        fotos = self._get_fotos_list(obj)
        return fotos[0] if fotos else None

    def get_total_fotos(self, obj):
        return len(self._get_fotos_list(obj))

    def get_veterinario_nombre(self, obj):
        vet = getattr(obj, 'id_veterinario', None)
        if not vet:
            return ''
        return f"{getattr(vet, 'nombre', '') or ''} {getattr(vet, 'apellido', '') or ''}".strip()

    def get_lugar_atencion(self, obj):
        ubi = getattr(obj, 'id_ubicacion_visita', None)
        if not ubi:
            return None
        return {
            'id_ubicacion': ubi.id_ubicacion,
            'direccion': ubi.direccion,
            'latitud': ubi.latitud,
            'longitud': ubi.longitud,
        }

    def get_total_funcionarios(self, obj):
        # Usa el prefetch cuando existe para no generar N+1
        funcionarios = getattr(obj, 'funcionarios', None)
        try:
            if funcionarios is not None and hasattr(funcionarios, 'all'):
                prefetched = getattr(obj, '_prefetched_objects_cache', {}).get('funcionarios')
                if prefetched is not None:
                    return len(prefetched)
                return funcionarios.count()
        except Exception:
            pass
        return 0

    def _visitas_qs(self, obj):
        # VisitaAnimal no define related_name; se consulta por FK directa.
        # Se limita a 50 filas por acta para que el listado siga liviano.
        return (
            VisitaAnimal.objects
            .filter(id_seguimiento_id=obj.pk)
            .select_related('id_animal')
            .order_by('id_visita')[:50]
        )

    def get_animales(self, obj):
        return VisitaAnimalListSerializer(self._visitas_qs(obj), many=True).data

    def get_total_animales(self, obj):
        if obj.nro_animales_atendidos:
            try:
                return int(obj.nro_animales_atendidos)
            except (TypeError, ValueError):
                pass
        try:
            count = VisitaAnimal.objects.filter(id_seguimiento_id=obj.pk).count()
        except Exception:
            count = 0
        if count:
            return count
        # Actas viejas de trazabilidad (ej. cambio de estado) o modo A con animal único
        if getattr(obj, 'id_animal_id', None):
            return 1
        return 0

