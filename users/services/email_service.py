import os
import requests

def enviar_correo_transaccional_brevo(destinatario_email, destinatario_nombre, asunto, contenido_html):
    """
    Envía un correo transaccional usando la API HTTP de Brevo (puerto 443).
    """
    url = "https://api.brevo.com/v3/smtp/email"
    api_key = os.environ.get("BREVO_API_KEY")
    remitente = os.environ.get("DEFAULT_FROM_EMAIL")

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }

    payload = {
        "sender": {"name": "Sistema Django", "email": remitente},
        "to": [{"email": destinatario_email, "name": destinatario_nombre}],
        "subject": asunto,
        "htmlContent": contenido_html,
    }

    try:
        respuesta = requests.post(url, json=payload, headers=headers, timeout=10)
        if respuesta.status_code == 201:
            return {"exito": True, "data": respuesta.json()}
        else:
            return {"exito": False,
                    "error": f"Error Brevo API [{respuesta.status_code}]: {respuesta.text}"}
    except requests.exceptions.Timeout:
        return {"exito": False,
                "error": "La petición a Brevo excedió el tiempo límite (Timeout)."}
    except requests.exceptions.RequestException as e:
        return {"exito": False,
                "error": f"Excepción de red al conectar con Brevo: {str(e)}"}