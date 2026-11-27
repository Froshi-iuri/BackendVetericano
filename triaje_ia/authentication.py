import hmac
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

HEADER_TRIAJE_SERVICE_TOKEN = 'HTTP_X_VETERICANO_TRIAJE_TOKEN'


class TriajeServicePrincipal:
    """Identidad técnica con privilegios mínimos exclusivos para el servicio de triaje n8n."""

    id_usuario = None
    pk = None
    activo = True
    is_authenticated = True
    is_anonymous = False
    es_servicio_triaje = True

    def __str__(self):
        return 'TriajeN8nServicePrincipal'


class TriajeServiceTokenAuthentication(BaseAuthentication):
    """
    Autentica peticiones internas provenientes de n8n mediante un token de servicio
    configurado en `settings.TRIAJE_SERVICE_TOKEN` (fail-closed si está vacío).
    Acepta `X-Vetericano-Triaje-Token: <token>` o `Authorization: Bearer <token>`.
    """

    keyword = 'Bearer'

    def authenticate(self, request):
        token_enviado = (request.META.get(HEADER_TRIAJE_SERVICE_TOKEN) or '').strip()

        if not token_enviado:
            auth_header = (request.META.get('HTTP_AUTHORIZATION') or '').strip()
            if auth_header:
                partes = auth_header.split(None, 1)
                if len(partes) != 2 or partes[0].lower() != self.keyword.lower():
                    raise AuthenticationFailed('Formato de cabecera Authorization inválido.')
                token_enviado = partes[1].strip()

        if not token_enviado:
            return None

        token_esperado = str(getattr(settings, 'TRIAJE_SERVICE_TOKEN', '') or '').strip()
        if not token_esperado:
            raise AuthenticationFailed('El servicio interno de triaje no tiene token configurado.')

        if not hmac.compare_digest(token_enviado.encode('utf-8'), token_esperado.encode('utf-8')):
            raise AuthenticationFailed('Token de servicio de triaje inválido.')

        return (TriajeServicePrincipal(), {'servicio': 'n8n-triaje'})

    def authenticate_header(self, request):
        return 'Bearer realm="triaje-interno"'
