from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ("survey", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX IF NOT EXISTS text_answer_embedding_hnsw_idx
            ON "TextAnswerEmbedding"
            USING hnsw (embedding vector_cosine_ops);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS text_answer_embedding_hnsw_idx;
            """,
        ),
    ]