from django.shortcuts import render

# AnaC

import random
import hashlib
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .models import Usuarios

def encriptar_password(password):
    """Genera un hash seguro compatible con la verificación estándar."""
    salt = secrets.token_hex(16)
    pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), 100000)
    return f"pbkdf2_sha256$100000${salt}${pwdhash.hex()}"

@api_view(['POST'])
def solicitar_recuperacion(request):
    email = request.data.get('email')
    
    if not email:
        return Response({'error': 'Por favor ingresa un correo electrónico.'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        user = Usuarios.objects.get(email__iexact=email)
    except Usuarios.DoesNotExist:
        return Response({'error': 'No existe un usuario con este correo.'}, status=status.HTTP_404_NOT_FOUND)
    
    codigo = str(random.randint(100000, 999999))
    cache.set(f'recuperacion_{email}', codigo, timeout=900)
    
    asunto = 'Código de recuperación de contraseña'
    cuerpo = f'Hola, tu código para recuperar la contraseña es: {codigo}'
    
    try:
        # Configuración SMTP directa con Python (Bypasa completamente el sistema MAILERS de Django)
        remitente = 'vetericano@gmail.com'
        password_app = 'vfug poqj pzjv ssae'
        
        mensaje = MIMEMultipart()
        mensaje['From'] = remitente
        mensaje['To'] = email
        mensaje['Subject'] = asunto
        mensaje.attach(MIMEText(cuerpo, 'plain'))
        
        servidor = smtplib.SMTP('smtp.gmail.com', 587)
        servidor.starttls()
        servidor.login(remitente, password_app)
        servidor.sendmail(remitente, email, mensaje.as_string())
        servidor.quit()
        
        return Response({'mensaje': 'Correo enviado exitosamente.'}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': f'Error técnico: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def confirmar_recuperacion(request):
    email = request.data.get('email')
    codigo = request.data.get('codigo')
    nueva_password = request.data.get('nueva_password')
    
    if not email or not codigo or not nueva_password:
        return Response({'error': 'Todos los campos son obligatorios.'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        user = Usuarios.objects.get(email__iexact=email)
    except Usuarios.DoesNotExist:
        return Response({'error': 'Usuario no encontrado.'}, status=status.HTTP_404_NOT_FOUND)
    
    codigo_guardado = cache.get(f'recuperacion_{email}')
    
    if not codigo_guardado or codigo_guardado != codigo:
        return Response({'error': 'El código de recuperación es incorrecto o ha expirado.'}, status=status.HTTP_400_BAD_REQUEST)
    
    # Actualizar la contraseña con el hash seguro
    user.password = encriptar_password(nueva_password)
    user.save(update_fields=['password'])
    
    cache.delete(f'recuperacion_{email}')
    
    return Response({'mensaje': 'Contraseña actualizada exitosamente.'}, status=status.HTTP_200_OK)