"""Data-access helpers: PostgreSQL (tenant-scoped transactions), DocumentDB/MongoDB, and S3."""
from contextlib import contextmanager
from pathlib import Path

import boto3
from botocore.config import Config
from sqlalchemy import create_engine, text

from . import config

engine = create_engine(config.DATABASE_URL, pool_size=10, max_overflow=5, pool_pre_ping=True)


@contextmanager
def tx(tenant_id: str | None, role: str = ""):
    """One transaction with RLS context: SET LOCAL app.tenant_id / app.role (see schema.sql)."""
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true), set_config('app.role', :r, true),"
                 " set_config('statement_timeout', '5000', true)"),
            {"t": tenant_id or "", "r": role},
        )
        yield conn


def rows(result):
    return [dict(r._mapping) for r in result]


def migrate():
    """Create tables and policies. An advisory lock stops parallel tasks from racing."""
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(281)"))
        with conn.connection.dbapi_connection.cursor() as cur:   # raw cursor: no %-placeholder parsing
            cur.execute(sql)


# ---------------- MongoDB / DocumentDB
_mongo = None


def mongo():
    global _mongo
    if _mongo is None:
        if config.MONGO_URL.startswith("mongomock://"):        # unit tests only
            import mongomock
            _mongo = mongomock.MongoClient()[config.MONGO_DB]
        else:
            from pymongo import MongoClient
            _mongo = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=3000)[config.MONGO_DB]
    return _mongo


# ---------------- S3
def _s3(endpoint: str | None):
    # MinIO (local): path-style on its own endpoint. AWS: regional virtual-host endpoint, so presigned
    # URLs work immediately for new buckets outside us-east-1.
    style = "path" if endpoint else "virtual"
    return boto3.client("s3", region_name=config.AWS_REGION,
                        endpoint_url=endpoint or f"https://s3.{config.AWS_REGION}.amazonaws.com",
                        config=Config(signature_version="s3v4", s3={"addressing_style": style}))


s3 = _s3(config.S3_ENDPOINT or None)                                        # server-side calls
s3_public = _s3(config.S3_PUBLIC_ENDPOINT or config.S3_ENDPOINT or None)   # URLs handed to browsers


def presign_get(key: str, ttl: int = config.EVIDENCE_URL_TTL) -> str:
    return s3_public.generate_presigned_url(
        "get_object", Params={"Bucket": config.S3_BUCKET, "Key": key}, ExpiresIn=ttl)


def presign_put(key: str, content_type: str, ttl: int = 600) -> str:
    return s3_public.generate_presigned_url(
        "put_object", Params={"Bucket": config.S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=ttl)


def ensure_bucket():
    """Local only (MinIO): create the media bucket if missing. In AWS the CDK stack creates it."""
    if not config.S3_ENDPOINT:
        return
    try:
        s3.head_bucket(Bucket=config.S3_BUCKET)
    except Exception:
        extra = ({} if config.AWS_REGION == "us-east-1"
                 else {"CreateBucketConfiguration": {"LocationConstraint": config.AWS_REGION}})
        s3.create_bucket(Bucket=config.S3_BUCKET, **extra)
