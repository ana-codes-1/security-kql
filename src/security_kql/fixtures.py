import hashlib
import json
from datetime import datetime

from security_kql.config import PACKAGE

FIXTURE_PATH = PACKAGE / "data" / "fixtures.json"


def load_fixtures():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture_hash():
    return hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def expected_count(vulnerability_id, fixtures=None):
    fixtures = fixtures or load_fixtures()
    cutoff = datetime.fromisoformat(fixtures["as_of"])
    latest = {}
    for finding in fixtures["findings"]:
        timestamp = datetime.fromisoformat(finding["ObservedAt"])
        if finding["VulnerabilityId"] != vulnerability_id or timestamp > cutoff:
            continue
        device = finding["DeviceId"]
        if device not in latest or timestamp > datetime.fromisoformat(latest[device]["ObservedAt"]):
            latest[device] = finding
    affected_devices = {device for device, row in latest.items() if row["Status"] == "Open"}
    return len(
        {
            row["UserId"]
            for row in fixtures["device_users"]
            if row["DeviceId"] in affected_devices and row["UserId"]
        }
    )
