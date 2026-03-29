# Democracy-API

```
psql -U postgres -d [database name]
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

```
SECRET_KEY='SECRET_KEY'
DEBUG=True
MODE='dev'
ALLOWED_HOSTS=localhost,127.0.0.1

# Postgresql DB
DB_NAME='DB_NAME'
DB_USER='DB_USER'
DB_PASSWORD='DB_PASSWORD'
DB_HOST='127.0.0.1'
DB_PORT='5432'
```

```
cd docker
docker-compose config 
docker compose up --build
```
