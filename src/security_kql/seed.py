import json

from security_kql.fixtures import load_fixtures

TABLES = {
    "DeviceUsers": (
        [("DeviceId", "string"), ("UserId", "string")],
        "device_users",
    ),
    "VulnerabilityFindings": (
        [
            ("ObservationId", "string"),
            ("DeviceId", "string"),
            ("VulnerabilityId", "string"),
            ("Status", "string"),
            ("ObservedAt", "datetime"),
            ("SensorId", "string"),
        ],
        "findings",
    ),
}


def seed(clients):
    fixtures = load_fixtures()
    for table, (columns, fixture_key) in TABLES.items():
        schema = ", ".join(f"{name}:{kind}" for name, kind in columns)
        clients.kusto.execute_mgmt(
            clients.settings.database, f".create-merge table {table} ({schema})"
        )
        values = []
        for row in fixtures[fixture_key]:
            for name, kind in columns:
                value = row[name]
                values.append(f"datetime({value})" if kind == "datetime" else json.dumps(value))
        statement = f".set-or-replace {table} <| datatable({schema}) [{', '.join(values)}]"
        clients.kusto.execute_mgmt(clients.settings.database, statement)
