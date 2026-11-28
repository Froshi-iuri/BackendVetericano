import io

from PIL import Image, ImageDraw


MARCADOR_PRUEBA_TRIAJE = '[PRUEBA-TRIAJE]'
ESCENARIOS_DEMO = {
    'baja': '[PRUEBA-TRIAJE] En esta simulación, un perro sano está alerta y camina con normalidad. '
            'Se solicita un registro preventivo de rutina.',
    'media': '[PRUEBA-TRIAJE] En esta simulación, un perro permanece en un recinto estrecho '
             'sin sombra durante varias horas. Está alerta y camina con normalidad.',
    'urgente': '[PRUEBA-TRIAJE] En esta simulación, un perro queda inmóvil junto a un vehículo '
               'tras un atropellamiento. Se reporta sangrado activo en una pata.',
}
ESCENARIO_CON_IMAGEN = 'urgente'


def dibujar_imagen_demo() -> bytes:
    """Ilustración de un escenario fijo, generada sin archivos ni fotografías externas."""
    imagen = Image.new('RGB', (640, 400), '#e0f2fe')
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rectangle((0, 215, 640, 400), fill='#64748b')
    dibujo.rectangle((0, 345, 640, 358), fill='#fef3c7')
    dibujo.rounded_rectangle((350, 145, 614, 249), radius=22, fill='#0369a1')
    dibujo.polygon([(397, 152), (435, 106), (539, 106), (579, 152)], fill='#075985')
    dibujo.polygon([(425, 145), (449, 117), (526, 117), (553, 145)], fill='#bae6fd')
    for x in (407, 550):
        dibujo.ellipse((x, 224, x + 54, 278), fill='#172554')
        dibujo.ellipse((x + 16, 240, x + 38, 262), fill='#cbd5e1')
    dibujo.line([(252, 266), (293, 247), (310, 228)], fill='#78350f', width=14)
    dibujo.ellipse((143, 246, 270, 297), fill='#a16207')
    dibujo.line([(173, 283), (142, 310), (111, 312)], fill='#a16207', width=15)
    dibujo.line([(234, 283), (265, 308), (299, 308)], fill='#a16207', width=15)
    dibujo.ellipse((94, 244, 164, 297), fill='#b45309')
    dibujo.ellipse((71, 266, 122, 293), fill='#d97706')
    dibujo.ellipse((70, 266, 83, 278), fill='#1c1917')
    dibujo.ellipse((127, 242, 151, 281), fill='#78350f')
    dibujo.ellipse((107, 258, 114, 265), fill='#1c1917')
    dibujo.ellipse((274, 303, 303, 315), fill='#b91c1c')
    dibujo.ellipse((288, 315, 318, 326), fill='#b91c1c')
    dibujo.text((24, 24), 'ILUSTRACION SINTETICA - SIMULACION', fill='#0f172a')
    salida = io.BytesIO()
    imagen.save(salida, format='PNG')
    return salida.getvalue()
