from rest_framework import viewsets
from ..models import AdopcionAnimal, SolicitudAdopcion
from .serializers import AdopcionAnimalSerializer, SolicitudAdopcionSerializer
from django.core.mail import send_mail
from django.conf import settings

class AdopcionAnimalViewSet(viewsets.ModelViewSet):
    queryset = AdopcionAnimal.objects.all()
    serializer_class = AdopcionAnimalSerializer

class SolicitudAdopcionViewSet(viewsets.ModelViewSet):
    queryset = SolicitudAdopcion.objects.all()
    serializer_class = SolicitudAdopcionSerializer

    def perform_create(self, serializer):
        # Guarda la solicitud en la base de datos
        solicitud = serializer.save()
        
        # Prepara los datos para la notificación al área jurídica
        asunto = f"Nueva solicitud de adopción para: {solicitud.animal.nombre}"
        mensaje = (
            f"¡Hola equipo jurídico!\n\n"
            f"Se ha registrado una nueva solicitud de adopción con los siguientes datos:\n\n"
            f"--- DATOS DE LA MASCOTA ---\n"
            f"Nombre: {solicitud.animal.nombre}\n"
            f"Raza: {solicitud.animal.raza}\n\n"
            f"--- DATOS DEL INTERESADO ---\n"
            f"Nombre: {solicitud.nombre_adoptante}\n"
            f"Cédula: {solicitud.cedula}\n"
            f"Correo: {solicitud.correo_adoptante}\n"
            f"Teléfono: {solicitud.telefono}\n"
            f"Mensaje: {solicitud.mensaje}\n\n"
            f"Por favor, procedan a contactar al adoptante."
        )
        remitente = settings.DEFAULT_FROM_EMAIL
        destinatarios = ['juridico@vetericano.com']

        try:
            send_mail(asunto, mensaje, remitente, destinatarios, fail_silently=False)
        except Exception as e:
            print(f"Error al enviar el correo: {e}")