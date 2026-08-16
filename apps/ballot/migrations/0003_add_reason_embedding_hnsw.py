from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ballot", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX IF NOT EXISTS reason_embedding_hnsw_idx
            ON "ReasonEmbedding"
            USING hnsw (embedding vector_cosine_ops);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS reason_embedding_hnsw_idx;
            """,
        ),
    ]