from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import RegisterSerializer

class LoginView(APIView):
    # EL PORQUÉ: El login procesa credenciales privadas, por lo que debe responder exclusivamente a solicitudes POST.
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response({'error': 'El correo y la contraseña son obligatorios'}, status=status.HTTP_400_BAD_REQUEST)

        # EL PORQUÉ: Buscamos al usuario por su correo para obtener su 'username' interno (su cédula) y hacer la autenticación oficial.
        try:
            user_obj = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({'error': 'Su correo electrónico y su contraseña no coinciden. Inténtelo de nuevo'}, status=status.HTTP_401_UNAUTHORIZED)

        user = authenticate(username=user_obj.username, password=password)

        if user is not None:
            # EL PORQUÉ: Generamos las llaves encriptadas JWT para que el teléfono mantenga la sesión iniciada sin guardar contraseñas locales.
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user_id': user.id,
                'email': user.email,
                'first_name': user.first_name,
                'is_staff': user.is_staff
            }, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Su correo electrónico y su contraseña no coinciden. Inténtelo de nuevo'}, status=status.HTTP_401_UNAUTHORIZED)


class RegisterView(APIView):
    # EL PORQUÉ: Recibe el formulario de registro desde la app móvil para crear el nuevo perfil cliente.
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        
        if serializer.is_valid():
            user = serializer.save()
            
            # EL PORQUÉ: Le devolvemos el token JWT al momento de crear la cuenta para que no tenga que ir a loguearse manualmente.
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user_id': user.id,
                'email': user.email,
                'first_name': user.first_name,
                'is_staff': user.is_staff
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)