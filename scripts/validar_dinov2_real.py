r"""
Script de validación real de DINOv2 Small (`facebook/dinov2-small`) en BackendVetericano.

No escribe en base de datos, no ejecuta migraciones y no activa DINOv2 globalmente
en `settings.py`. Permite medir tiempos reales de carga del modelo, inferencia por
fotografía y comparación de similitud visual (mismo animal desde distintos ángulos vs.
animales diferentes).

Uso recomendado:
  1) Colocar fotografías reales en `BackendVetericano/scripts/fotos_prueba_dinov2/`:
       - `mismo_animal_1.jpg` (o .jpeg / .png / .webp)
       - `mismo_animal_2.jpg` (mismo animal desde otro ángulo)
       - `animal_diferente_1.jpg` (uno o más archivos `animal_diferente*.jpg|png|webp`)
     y ejecutar:
       .\envs\Scripts\python.exe scripts\validar_dinov2_real.py

  2) O pasar rutas explícitas / URLs de Cloudinary:
       .\envs\Scripts\python.exe scripts\validar_dinov2_real.py ^
         --mismo-animal ruta\foto_angulo1.jpg ruta\foto_angulo2.jpg ^
         --diferentes ruta\otro_animal.jpg
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FOTOS_DIR = Path(__file__).resolve().parent / 'fotos_prueba_dinov2'
EXTENSIONES_VALIDAS = {'.jpg', '.jpeg', '.png', '.webp'}


def _configurar_django() -> None:
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vetericano.settings')
    import django

    django.setup()


def _es_url(valor: str) -> bool:
    return valor.startswith('https://') or valor.startswith('http://')


def _buscar_fotos_en_directorio(directorio: Path) -> Tuple[List[str], List[str]]:
    if not directorio.exists() or not directorio.is_dir():
        return [], []

    archivos = sorted(
        p for p in directorio.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES_VALIDAS
    )
    mismo_animal: List[str] = []
    diferentes: List[str] = []
    for p in archivos:
        nombre = p.stem.lower()
        if nombre.startswith('mismo_animal'):
            mismo_animal.append(str(p))
        elif nombre.startswith('animal_diferente') or nombre.startswith('diferente'):
            diferentes.append(str(p))
    return mismo_animal, diferentes


def _procesar_origen(origen: str, *, umbral_bytes: int) -> Dict[str, object]:
    from peticiones.services import dinov2_service, imagen_hash_service

    t0 = time.perf_counter()
    if _es_url(origen):
        url_segura = imagen_hash_service.validar_url_cloudinary_segura(origen)
        if not url_segura:
            raise ValueError(f"URL rechazada por validación segura de Cloudinary: {origen}")
        contenido = imagen_hash_service.descargar_imagen_segura(
            url_segura,
            miniatura_ancho=256,
            miniatura_alto=256,
        )
        if contenido is None:
            raise ValueError(f"No se pudo descargar la imagen desde Cloudinary: {origen}")
    else:
        ruta = Path(origen)
        if not ruta.exists() or not ruta.is_file():
            raise FileNotFoundError(f"No existe el archivo de imagen: {origen}")
        contenido = ruta.read_bytes()

    imagen = imagen_hash_service.decodificar_y_validar_imagen_bytes(
        contenido,
        max_bytes=umbral_bytes,
    )
    if imagen is None:
        raise ValueError(f"Imagen inválida (formato no soportado, dañada, tamaño o dimensiones): {origen}")

    t_inf0 = time.perf_counter()
    embedding = dinov2_service.extraer_embedding_dinov2_desde_imagen(imagen)
    t_inf_ms = (time.perf_counter() - t_inf0) * 1000.0
    dhash = imagen_hash_service.calcular_dhash_desde_imagen(imagen)
    t_total_ms = (time.perf_counter() - t0) * 1000.0

    return {
        'origen': origen,
        'bytes': len(contenido),
        'dimensiones_rgb': list(imagen.size),
        'embedding_dim': len(embedding),
        'norma_l2': round(math.sqrt(sum(v * v for v in embedding)), 6),
        'tiempo_inferencia_ms': round(t_inf_ms, 2),
        'tiempo_total_ms': round(t_total_ms, 2),
        'dhash_hex': imagen_hash_service.formatear_dhash_hex(dhash),
        '_embedding': embedding,
        '_dhash': dhash,
    }


def _comparar_par(
    nombre_caso: str,
    item_a: Dict[str, object],
    item_b: Dict[str, object],
    umbral_dinov2: float,
) -> Dict[str, object]:
    from peticiones.services import dinov2_service, imagen_hash_service

    t0 = time.perf_counter()
    sim_coseno = dinov2_service.calcular_similitud_coseno(item_a['_embedding'], item_b['_embedding'])
    porcentaje = dinov2_service.calcular_porcentaje_similitud_visual(sim_coseno)
    comp_ms = (time.perf_counter() - t0) * 1000.0

    hamming = None
    if item_a.get('_dhash') is not None and item_b.get('_dhash') is not None:
        hamming = imagen_hash_service.calcular_distancia_hamming(item_a['_dhash'], item_b['_dhash'])

    return {
        'caso': nombre_caso,
        'imagen_a': Path(str(item_a['origen'])).name if not _es_url(str(item_a['origen'])) else item_a['origen'],
        'imagen_b': Path(str(item_b['origen'])).name if not _es_url(str(item_b['origen'])) else item_b['origen'],
        'similitud_coseno': round(sim_coseno, 4),
        'porcentaje_similitud_visual': porcentaje,
        'supera_umbral_dinov2': sim_coseno >= umbral_dinov2,
        'umbral_dinov2': umbral_dinov2,
        'es_certeza_identidad': False,
        'nota_metrica': dinov2_service.ADVERTENCIA_METRICA_VISUAL_DEFAULT,
        'distancia_hamming_dhash_ref': hamming,
        'tiempo_comparacion_ms': round(comp_ms, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Validación real de DINOv2 Small sin modificar base de datos ni settings globales.'
    )
    parser.add_argument(
        '--mismo-animal',
        nargs=2,
        metavar=('FOTO_ANGULO_1', 'FOTO_ANGULO_2'),
        help='Dos fotografías reales del mismo animal desde distintos ángulos.',
    )
    parser.add_argument(
        '--diferentes',
        nargs='+',
        metavar='FOTO_DIFERENTE',
        help='Una o más fotografías reales de animales diferentes.',
    )
    parser.add_argument(
        '--directorio',
        default=str(DEFAULT_FOTOS_DIR),
        help=f'Carpeta por defecto con imágenes de prueba ({DEFAULT_FOTOS_DIR}).',
    )
    parser.add_argument(
        '--solo-cargar-modelo',
        action='store_true',
        help='Solo verifica la descarga/carga real de facebook/dinov2-small y sale.',
    )
    args = parser.parse_args()

    _configurar_django()
    from django.conf import settings
    from peticiones.services import dinov2_service, imagen_hash_service

    cfg_dinov2 = dinov2_service.obtener_config_dinov2()
    cfg_img = imagen_hash_service.obtener_config_imagen_hash()
    print(f"[INFO] DUPLICADOS_IA_DINOV2_ACTIVA global en settings: {getattr(settings, 'DUPLICADOS_IA_DINOV2_ACTIVA', False)}")
    print(f"[INFO] Modelo configurado: {cfg_dinov2['model_name']} | device={cfg_dinov2['device']} | umbral={cfg_dinov2['umbral_dinov2']}")

    t_load0 = time.perf_counter()
    try:
        _, _, _, dev_real = dinov2_service._cargar_modelo_dinov2()
    except Exception as exc:
        print(json.dumps({
            'ok': False,
            'etapa': 'carga_modelo',
            'error': str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    t_load_ms = (time.perf_counter() - t_load0) * 1000.0
    print(f"[OK] Modelo {cfg_dinov2['model_name']} cargado en {t_load_ms:.2f} ms (device real={dev_real}).")

    if args.solo_cargar_modelo:
        return 0

    if args.mismo_animal and args.diferentes:
        mismo_animal = list(args.mismo_animal)
        diferentes = list(args.diferentes)
    else:
        mismo_dir, dif_dir = _buscar_fotos_en_directorio(Path(args.directorio))
        mismo_animal = list(args.mismo_animal) if args.mismo_animal else mismo_dir
        diferentes = list(args.diferentes) if args.diferentes else dif_dir

    if len(mismo_animal) < 2 or len(diferentes) < 1:
        print(
            "\n[PENDIENTE DE FOTOGRAFÍAS REALES]\n"
            f"Se requieren al menos 2 fotos del mismo animal y 1 de animal diferente.\n"
            f"Colócalas en: {Path(args.directorio)}\n"
            "  - mismo_animal_1.(jpg|jpeg|png|webp)\n"
            "  - mismo_animal_2.(jpg|jpeg|png|webp)\n"
            "  - animal_diferente_1.(jpg|jpeg|png|webp)\n"
            "O pásalas con --mismo-animal FOTO1 FOTO2 --diferentes FOTO3."
        )
        return 1

    resultados_imagenes: Dict[str, Dict[str, object]] = {}
    errores: List[Dict[str, str]] = []
    for ruta in mismo_animal + diferentes:
        if ruta in resultados_imagenes:
            continue
        try:
            resultados_imagenes[ruta] = _procesar_origen(ruta, umbral_bytes=cfg_img['max_bytes'])
        except Exception as exc:
            errores.append({'origen': ruta, 'error': str(exc)})

    comparaciones: List[Dict[str, object]] = []
    a1, a2 = mismo_animal[0], mismo_animal[1]
    if a1 in resultados_imagenes and a2 in resultados_imagenes:
        comparaciones.append(
            _comparar_par(
                'mismo_animal_distintos_angulos',
                resultados_imagenes[a1],
                resultados_imagenes[a2],
                cfg_dinov2['umbral_dinov2'],
            )
        )

    for idx, dif in enumerate(diferentes, start=1):
        if a1 in resultados_imagenes and dif in resultados_imagenes:
            comparaciones.append(
                _comparar_par(
                    f'animales_diferentes_{idx}',
                    resultados_imagenes[a1],
                    resultados_imagenes[dif],
                    cfg_dinov2['umbral_dinov2'],
                )
            )

    resumen_imagenes = [
        {k: v for k, v in datos.items() if not k.startswith('_')}
        for datos in resultados_imagenes.values()
    ]
    reporte = {
        'ok': len(errores) == 0 and len(comparaciones) > 0,
        'modelo': cfg_dinov2['model_name'],
        'device_real': dev_real,
        'ia_activa_global_settings': bool(getattr(settings, 'DUPLICADOS_IA_DINOV2_ACTIVA', False)),
        'tiempo_carga_modelo_ms': round(t_load_ms, 2),
        'imagenes_procesadas': resumen_imagenes,
        'comparaciones': comparaciones,
        'errores': errores,
    }
    print(json.dumps(reporte, ensure_ascii=False, indent=2))
    return 0 if reporte['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
