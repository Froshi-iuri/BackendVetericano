"""
Migración preparada (NO ejecutar sin autorización explícita) para almacenar
embeddings visuales de 384 dimensiones (DINOv2 Small) por fotografía de petición.

Evaluación e integración opcional de `pgvector` en PostgreSQL:
------------------------------------------------------------------
Cuando el servidor PostgreSQL disponga de la extensión `pgvector` (por ejemplo,
en Supabase / PostgreSQL >= 15 ejecutando `CREATE EXTENSION IF NOT EXISTS vector;`),
se recomienda complementar esta tabla con una columna nativa `vector(384)` e índice
HNSW para búsqueda aproximada por similitud coseno:

    CREATE EXTENSION IF NOT EXISTS vector;

    ALTER TABLE peticiones_imagen_embedding
        ADD COLUMN IF NOT EXISTS embedding_pgvector vector(384);

    CREATE INDEX IF NOT EXISTS idx_pet_img_emb_pgvector_hnsw
        ON peticiones_imagen_embedding
        USING hnsw (embedding_pgvector vector_cosine_ops);

Ventajas evaluadas de `pgvector` frente a reprocesar Cloudinary en cada consulta:
1. Evita descargas repetidas y re-inferencia PyTorch/DINOv2 sobre fotografías
   de los últimos N días, reduciendo la latencia de segundos a pocos milisegundos.
2. El operador `<=>` (distancia coseno) sobre vectores L2-normalizados con índice
   HNSW escala a miles de reportes sin degradar el endpoint ciudadano.
3. Si `pgvector` no está habilitado aún en la base de datos de producción, el campo
   `embedding_vector` (JSONField) conserva el vector de 384 floats en formato
   estándar para cálculo exacto en memoria hasta activar la extensión.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('peticiones', '0006_peticionfotoautorizada'),
    ]

    operations = [
        migrations.CreateModel(
            name='PeticionImagenEmbedding',
            fields=[
                ('id_embedding', models.BigAutoField(primary_key=True, serialize=False)),
                (
                    'url_imagen',
                    models.CharField(max_length=500),
                ),
                (
                    'modelo_ia',
                    models.CharField(default='facebook/dinov2-small', max_length=100),
                ),
                ('dimensiones', models.PositiveSmallIntegerField(default=384)),
                (
                    'embedding_vector',
                    models.JSONField(
                        help_text='Vector L2-normalizado de 384 dimensiones (lista de floats); compatible con pgvector vector(384).'
                    ),
                ),
                ('dhash_64', models.BigIntegerField(blank=True, null=True)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                (
                    'peticion',
                    models.ForeignKey(
                        db_column='id_peticion',
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='embeddings_imagenes',
                        to='peticiones.peticiones',
                    ),
                ),
            ],
            options={
                'db_table': 'peticiones_imagen_embedding',
                'managed': True,
                'indexes': [
                    models.Index(fields=['peticion'], name='idx_pet_img_emb_peticion'),
                    models.Index(fields=['url_imagen'], name='idx_pet_img_emb_url'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('peticion', 'url_imagen', 'modelo_ia'),
                        name='uq_pet_img_emb_peticion_url_modelo',
                    ),
                ],
            },
        ),
    ]
