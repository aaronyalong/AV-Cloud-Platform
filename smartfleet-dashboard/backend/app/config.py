"""Runtime settings, read from environment variables (set by Docker Compose locally, by ECS in AWS)."""
import os
from urllib.parse import quote_plus

ENV = os.getenv("APP_ENV", "local")                 # local | aws
AUTH_MODE = os.getenv("AUTH_MODE", "local")         # local (dev tokens) | cognito
DEV_SECRET = os.getenv("DEV_SECRET", "dev-only-secret-change-me")
SIM_API_KEY = os.getenv("SIM_API_KEY", "sim-local-key")

# --- Cognito (AUTH_MODE=cognito)
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")

# --- PostgreSQL (RDS in AWS)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "smartfleet")
DB_USER = os.getenv("DB_USER", "dash")
DB_PASSWORD = os.getenv("DB_PASSWORD", "dashpass")
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")
DATABASE_URL = (
    f"postgresql+psycopg://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode={DB_SSLMODE}"
)

# --- DocumentDB / MongoDB (telemetry)
DOCDB_HOST = os.getenv("DOCDB_HOST", "")
DOCDB_USER = os.getenv("DOCDB_USER", "")
DOCDB_PASSWORD = os.getenv("DOCDB_PASSWORD", "")
if DOCDB_HOST:
    MONGO_URL = (
        f"mongodb://{quote_plus(DOCDB_USER)}:{quote_plus(DOCDB_PASSWORD)}@{DOCDB_HOST}:27017/"
        "?tls=true&tlsCAFile=/app/global-bundle.pem&replicaSet=rs0"
        "&readPreference=secondaryPreferred&retryWrites=false"
    )
else:
    MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "smartfleet")

# --- S3 (MinIO locally)
S3_BUCKET = os.getenv("S3_BUCKET", "smartfleet-media")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")                  # internal endpoint (MinIO); empty in AWS
S3_PUBLIC_ENDPOINT = os.getenv("S3_PUBLIC_ENDPOINT", "")    # endpoint the browser can reach (MinIO); empty in AWS
EVIDENCE_URL_TTL = 300                                      # BR-4: presigned URLs expire in 300 s

DEFAULT_TZ = os.getenv("DEFAULT_TZ", "America/Los_Angeles")
