# Democracy-API

```
psql -U postgres -d [database name]
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
```

```
# apps/ballot/migrations/000X_add_reason_embedding_hnsw.py

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ballot", "000X_previous"),
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
```

```
# apps/survey/migrations/000X_add_text_answer_embedding_hnsw.py

from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ("survey", "000X_previous_migration"),
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
```

```
# Sentry
SENTRY_DSN='SENTRY_DSN'
SENTRY_RELEASE='democracy@1.0.0'

# Django
SECRET_KEY='SECRET_KEY'
DEBUG=True
MODE='dev'
ALLOWED_HOSTS=localhost,127.0.0.1
ORIGINS=http://localhost:5500

# Postgresql DB
DB_NAME='DB_NAME'
DB_USER='DB_USER'
DB_PASSWORD='DB_PASSWORD'
DB_HOST='127.0.0.1'
DB_PORT='5432'

#FLOWER
FLOWER_USERNAME='FLOWER_USERNAME'
FLOWER_PASSWORD='FLOWER_PASSWORD'

# AWS
AWS_ACCESS_KEY_ID='AWS_ACCESS_KEY_ID'
AWS_SECRET_ACCESS_KEY='AWS_SECRET_ACCESS_KEY'
AWS_STORAGE_BUCKET_NAME='AWS_STORAGE_BUCKET_NAME'
AWS_S3_REGION_NAME='AWS_S3_REGION_NAME'
AWS_S3_ENDPOINT_URL='AWS_S3_ENDPOINT_URL'
AWS_S3_CUSTOM_DOMAIN='AWS_S3_CUSTOM_DOMAIN'

# AGORA
AGORA_APP_ID='AGORA_APP_ID'
AGORA_APP_CERTIFICATE='AGORA_APP_CERTIFICATE'
AGORA_CUSTOMER_ID='AGORA_CUSTOMER_ID'
AGORA_CUSTOMER_SECRET='AGORA_CUSTOMER_SECRET'
MEETING_PERIOD=3600

# Cloudflare
TUNNEL_TOKEN='TUNNEL_TOKEN'


```

```
cd docker
docker-compose config 
docker compose up --build

docker compose --profile monitoring up -d flower
```

### Ballots
```
Generate an already-ended ballot
python manage.py generate_test_ballot --users=120 --ended

Generate and queue Celery summarization
python manage.py generate_test_ballot --users=120 --ended --queue-summary

Generate and summarize immediately
python manage.py generate_test_ballot --users=120 --ended --summarize

Small fast test
python manage.py generate_test_ballot --users=20 --reason-rate=1.0 --ended --summarize

Larger clustering-style test
python manage.py generate_test_ballot --users=1000 --reason-rate=0.9 --ended --queue-summary

No fake PII
python manage.py generate_test_ballot --users=120 --pii-rate=0.0 --ended --summarize

Mostly PII-heavy test
python manage.py generate_test_ballot --users=50 --reason-rate=1.0 --pii-rate=1.0 --ended --summarize
```

### Surveys
```
Generate more text-heavy responses
python manage.py generate_test_survey --responses=150 --text-rate=1.0 --ended

python manage.py generate_test_survey --responses=200 --text-rate=1.0 --pii-rate=0.3 --ended

Generate responses with lots of fake PII
python manage.py generate_test_survey --responses=50 --text-rate=1.0 --pii-rate=0.8

docker compose exec web python manage.py generate_test_survey --responses=80 --ended
docker compose exec web python manage.py generate_test_survey --responses=200 --text-rate=1.0 --pii-rate=0.3
```