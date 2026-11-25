import hashlib
import re
from enum import StrEnum

from security_kql.config import PACKAGE
from security_kql.fixtures import fixture_hash, load_fixtures

DEFINITION = (
    "Count distinct nonempty UserId values associated with a device whose latest observation "
    "for the specified vulnerability, at or before the fixed snapshot time, is Open. "
    "Repeated scans and multiple affected devices for one user count only once."
)
SCHEMA_VERSION = "security-schema-v1"
PROMPT_VERSION = "affected-users-v1"
IDENTIFIER = re.compile(r"\bDEMO-2026-\d{4}\b", re.IGNORECASE)


class QueryKind(StrEnum):
    CANDIDATE = "candidate"
    REFERENCE = "reference"
    KNOWN_BAD = "known_bad"


def query_text(kind: QueryKind):
    if not isinstance(kind, QueryKind):
        raise ValueError("Only registered query kinds may execute.")
    return (PACKAGE / "queries" / f"{kind.value}.kql").read_text(encoding="utf-8")


def validate_identifier(value):
    known_ids = {row["id"] for row in load_fixtures()["vulnerabilities"]}
    if value not in known_ids:
        raise ValueError("Choose a vulnerability from the synthetic demo dataset.")
    return value


def reference_fingerprint():
    content = DEFINITION + query_text(QueryKind.REFERENCE) + fixture_hash()
    return hashlib.sha256(content.encode()).hexdigest()
