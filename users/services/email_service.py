import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests


def construir_plantilla_otp_vetericano(*, nombre: str, codigo: str, proposito: str = "verificacion") -> str:
    """
    Construye una plantilla HTML institucional limpia para el envío de códigos OTP
    de verificación de registro o recuperación de contraseña en Vetericano.
    """
    nombre_seguro = html.escape((nombre or "Usuario").strip() or "Usuario")
    codigo_seguro = html.escape(str(codigo).strip())

    if proposito == "registro":
        titulo = "Confirma tu correo electrónico"
        subtitulo = "Estás a un paso de crear tu cuenta en Vetericano."
        instruccion = (
            "Ingresa el siguiente código de verificación de 6 dígitos en la aplicación "
            "para confirmar que este correo electrónico es real y activar tu cuenta:"
        )
        nota_pie = "Si no intentaste registrarte en Vetericano, puedes ignorar este mensaje con total tranquilidad."
    else:
        titulo = "Recuperación de contraseña"
        subtitulo = "Solicitud de restablecimiento de clave en Vetericano."
        instruccion = (
            "Ingresa el siguiente código de seguridad de 6 dígitos en la aplicación "
            "para restablecer tu contraseña:"
        )
        nota_pie = "Si no solicitaste un cambio de contraseña, ignora este correo; tu cuenta sigue protegida."

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <title>{titulo}</title>
</head>
<body style="margin:0;padding:0;background-color:#F4F6FB;font-family:'Segoe UI',Arial,sans-serif;color:#170B3D;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:28px 14px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;background-color:#FFFFFF;border-radius:16px;border:1px solid #E2E8F0;overflow:hidden;">
          <tr>
            <td style="background-color:#170B3D;padding:22px 28px;text-align:center;">
              <span style="display:inline-block;background-color:#F1C63C;color:#162038;font-size:11px;font-weight:700;padding:4px 12px;border-radius:999px;text-transform:uppercase;letter-spacing:0.8px;">
                Centro de Bienestar Animal
              </span>
              <h1 style="margin:12px 0 4px 0;color:#FFFFFF;font-size:22px;font-weight:800;">Vetericano</h1>
              <p style="margin:0;color:#CBD5E1;font-size:13px;">{subtitulo}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px;">
              <p style="margin:0 0 12px 0;font-size:15px;color:#1E293B;">Hola, <strong>{nombre_seguro}</strong>:</p>
              <p style="margin:0 0 22px 0;font-size:14px;line-height:1.55;color:#475569;">{instruccion}</p>
              <div style="background-color:#F8FAFC;border:2px dashed #4141A5;border-radius:12px;padding:18px;text-align:center;margin-bottom:22px;">
                <span style="font-size:30px;font-weight:800;letter-spacing:8px;color:#4141A5;font-family:'Courier New',monospace;">
                  {codigo_seguro}
                </span>
              </div>
              <p style="margin:0 0 16px 0;font-size:13px;color:#64748B;text-align:center;">
                ⏳ Este código es válido por <strong>10 minutos</strong> y tiene un límite de <strong>5 intentos</strong>.
              </p>
              <hr style="border:none;border-top:1px solid #E2E8F0;margin:20px 0;" />
              <p style="margin:0;font-size:12px;line-height:1.5;color:#94A3B8;text-align:center;">
                {nota_pie}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _enviar_fallback_gmail_smtp(destinatario_email, destinatario_nombre, asunto, contenido_html):
    """
    Respaldo por SMTP de Gmail (puerto 587 STARTTLS) para desarrollo local cuando
    no está definida la variable BREVO_API_KEY en el archivo .env local.
    """
    remitente = os.environ.get("EMAIL_HOST_USER") or os.environ.get("DEFAULT_FROM_EMAIL") or "vetericano@gmail.com"
    password_app = os.environ.get("EMAIL_HOST_PASSWORD") or "vfug poqj pzjv ssae"
    host = os.environ.get("EMAIL_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("EMAIL_PORT") or 587)
    if port == 465:
        port = 587

    try:
        mensaje = MIMEMultipart("alternative")
        mensaje["From"] = f"Vetericano - Centro de Bienestar Animal <{remitente}>"
        mensaje["To"] = destinatario_email
        mensaje["Subject"] = asunto
        mensaje.attach(MIMEText(contenido_html, "html", "utf-8"))

        servidor = smtplib.SMTP(host, port, timeout=12)
        servidor.starttls()
        servidor.login(remitente, password_app)
        servidor.sendmail(remitente, [destinatario_email], mensaje.as_string())
        servidor.quit()
        return {"exito": True, "data": {"provider": "gmail_smtp_fallback"}}
    except Exception as exc:
        return {"exito": False, "error": f"Error SMTP de respaldo: {str(exc)}"}


def enviar_correo_transaccional_brevo(destinatario_email, destinatario_nombre, asunto, contenido_html):
    """
    Envía un correo transaccional usando la API HTTP de Brevo (puerto 443).
    Si en desarrollo local no está configurada BREVO_API_KEY, utiliza automáticamente
    el respaldo SMTP de Gmail para que la verificación funcione tanto en local como en Railway.
    """
    url = "https://api.brevo.com/v3/smtp/email"
    api_key = (os.environ.get("BREVO_API_KEY") or "").strip()
    remitente = (os.environ.get("DEFAULT_FROM_EMAIL") or "vetericano@gmail.com").strip()

    if not api_key:
        return _enviar_fallback_gmail_smtp(destinatario_email, destinatario_nombre, asunto, contenido_html)

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }

    payload = {
        "sender": {"name": "Vetericano - Centro de Bienestar Animal", "email": remitente},
        "to": [{"email": destinatario_email, "name": destinatario_nombre}],
        "subject": asunto,
        "htmlContent": contenido_html,
    }

    try:
        respuesta = requests.post(url, json=payload, headers=headers, timeout=10)
        if respuesta.status_code == 201:
            return {"exito": True, "data": respuesta.json()}
        else:
            # Si Brevo falla por clave inválida en local, intentar respaldo SMTP
            fallback = _enviar_fallback_gmail_smtp(destinatario_email, destinatario_nombre, asunto, contenido_html)
            if fallback.get("exito"):
                return fallback
            return {"exito": False,
                    "error": f"Error Brevo API [{respuesta.status_code}]: {respuesta.text}"}
    except requests.exceptions.Timeout:
        return {"exito": False,
                "error": "La petición a Brevo excedió el tiempo límite (Timeout)."}
    except requests.exceptions.RequestException as e:
        return {"exito": False,
                "error": f"Excepción de red al conectar con Brevo: {str(e)}"}