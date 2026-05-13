"""Data access stubs.

In production the agent reads loan state from Postgres, reads borrower
documents from S3, and writes audit records to a durable store. The take-
home is not about implementing those stores — they exist here as thin
classes so the infrastructure layer (DB endpoint, S3 bucket, IAM, KMS)
has a concrete client to wire up.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    postgres: bool
    s3: bool

    @property
    def ok(self) -> bool:
        return self.postgres and self.s3


class Store:
    """Lazy clients for Postgres + S3."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._engine: Engine | None = None
        self._s3 = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self.settings.database_url, pool_pre_ping=True)
        return self._engine

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3", region_name=self.settings.aws_region)
        return self._s3

    def health(self) -> HealthStatus:
        return HealthStatus(postgres=self._check_pg(), s3=self._check_s3())

    def write_audit_record(self, payload: dict[str, Any]) -> str:
        """Persist an audit log row for this LLM invocation.

        Returns an opaque audit_id. The candidate's infrastructure design
        determines where this lands (Postgres table, DynamoDB, S3 + Athena,
        Glue + Iceberg, etc) — 7-year retention is the requirement.
        """
        audit_id = f"audit_{uuid.uuid4().hex}"
        record = {**payload, "audit_id": audit_id, "written_at": datetime.now(timezone.utc).isoformat()}
        # Stub: in production this is a transactional write to the audit store.
        logger.info("audit.write", extra={"audit": record})
        return audit_id

    # ----- internals -----

    def _check_pg(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("postgres health check failed: %s", exc)
            return False

    def _check_s3(self) -> bool:
        try:
            self.s3.head_bucket(Bucket=self.settings.s3_bucket)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3 health check failed: %s", exc)
            return False
