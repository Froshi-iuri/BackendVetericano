from django.shortcuts import render

# AnaC

import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .services.email_service import enviar_correo_transaccional_brevo
from django.core.cache import cache
from django.contrib.auth.hashers import make_password
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .models import Usuarios
import os
from dotenv import load_dotenv

load_dotenv()


@api_view(['POST'])
def solicitar_recuperacion(request):
    email = (request.data.get('email') or '').strip()
    
    if not email:
        return Response({'error': 'Por favor ingresa un correo electrónico.'}, status=status.HTTP_400_BAD_REQUEST)
    
    user = Usuarios.objects.filter(email__iexact=email).first()
    if not user:
        return Response({'error': 'No existe un usuario con este correo.'}, status=status.HTTP_404_NOT_FOUND)
    
    email_key = email.lower()
    codigo = str(random.randint(100000, 999999))
    cache.set(f'recuperacion_{email_key}', codigo, timeout=900)
    
    asunto = 'Código de recuperación de contraseña'
    cuerpo = f'Hola, tu código para recuperar la contraseña es: {codigo}'
    
    try:
        # ---------- Envío usando Brevo (puerto 443) ----------
        html_content = f"""<html><body>
        <p>Hola,</p>
        <p>Tu código de recuperación es: <strong>{codigo}</strong></p>
        <p>Este código es válido por 15 minutos.</p>
        </body></html>"""
        resultado = enviar_correo_transaccional_brevo(
            destinatario_email=user.email,
            destinatario_nombre=user.nombre or 'Usuario',
            asunto=asunto,
            contenido_html=html_content,
        )
        if resultado["exito"]:
            return Response({'mensaje': 'Correo de recuperación enviado exitosamente.'}, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Falló la verificación del resultado.', 'detalle': resultado["error"]}, status=status.HTTP_502_BAD_GATEWAY)

    except Exception as e:
        return Response({'error': f'Error técnico al enviar el correo: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def confirmar_recuperacion(request):
    email = (request.data.get('email') or '').strip()
    codigo = (request.data.get('codigo') or '').strip()
    nueva_password = (request.data.get('nueva_password') or '').strip()
    
    if not email or not codigo or not nueva_password:
        return Response({'error': 'Todos los campos son obligatorios.'}, status=status.HTTP_400_BAD_REQUEST)
    
    usuarios_coincidentes = Usuarios.objects.filter(email__iexact=email)
    if not usuarios_coincidentes.exists():
        return Response({'error': 'Usuario no encontrado.'}, status=status.HTTP_404_NOT_FOUND)
    
    email_key = email.lower()
    codigo_guardado = cache.get(f'recuperacion_{email_key}') or cache.get(f'recuperacion_{email}')
    
    if not codigo_guardado or str(codigo_guardado).strip() != codigo:
        return Response({'error': 'El código de recuperación es incorrecto o ha expirado.'}, status=status.HTTP_400_BAD_REQUEST)
    
    # Actualizar la contraseña con el hash seguro en todas las coincidencias de ese email
    password_hash = make_password(nueva_password)
    for u in usuarios_coincidentes:
        u.password = password_hash
        u.save(update_fields=['password'])
    
    cache.delete(f'recuperacion_{email_key}')
    cache.delete(f'recuperacion_{email}')
    
    return Response({'mensaje': 'Contraseña actualizada exitosamente.'}, status=status.HTTP_200_OK)