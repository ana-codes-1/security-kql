# Security KQL: Foundry observability demo

A local chat app interprets one security question, compiles parameterized KQL, and
executes it against a real Azure Data Explorer database. OpenTelemetry spans flow
to Application Insights and a registered external agent in Microsoft Foundry.
An exact-result evaluator compares the guarded candidate with a deliberately
incorrect saved baseline.

## Run

Requires Python 3.12+, Azure CLI authentication, and permission to deploy resources
and role assignments in the selected subscription.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
az login
.\scripts\deploy.ps1
.\scripts\initialize.ps1
.\scripts\start.ps1
```

Open **http://127.0.0.1:8017**. The app binds to loopback only; it is not an
authenticated, internet-facing service.

Deployment creates a dedicated resource group tagged `purpose=forescout-kql-demo`,
a development ADX cluster, a Foundry project and model deployment, Application
Insights, and a Log Analytics workspace. The cluster incurs charges while running.
Configuration is generated in `.env`; machine state and deployment output are
ignored by Git. No service keys are needed for model or database access: the local
app uses the current Azure CLI identity.

Initialization replaces only `DeviceUsers` and `VulnerabilityFindings`, verifies
the expected counts, and removes its temporary database Admin assignment. The
runtime retains database Viewer. Reseeding requires rerunning deployment to
recreate the temporary assignment, then initialization.

## Public Azure hosting

After the local reference has been explicitly reviewed and approved, deploy the
frontend and API together:

```powershell
.\scripts\deploy-web.ps1
```

This provisions Azure Container Apps, a private container registry, and a workload
identity in the existing dedicated resource group.
These are billed Azure resources. It builds the image in Azure and prints the
public HTTPS URL. Only explicitly selected production sources and dependency
manifests enter the build context, never the local `.env`, credentials, or trace
database. The app uses managed identity for Foundry and read-only ADX access.
One replica stays running. ADX data, Application Insights telemetry, completed
Foundry evaluations, and the deployed reference approval persist in Azure.
The container-local SQLite database is a temporary UI cache: active trace previews
and evaluation polling state reset on container replacement. Reload and rerun the
question/comparison after a restart; historical traces and evaluation reports
remain available in Foundry. No public or shared-key storage account is required.

The public site requires no sign-in. Reference approval is locked and copied only
from the existing reviewed local reference; changing the reference or fixtures
still invalidates it. Anonymous chat/comparison requests share a limit of 30 per
hour and two concurrent requests. Run one instance and one worker: this prototype
limit is process-local and resets on restart, not production abuse protection.
Requests are restricted to 8 KB. Foundry portal links still require Azure access.

## Two demo moments

1. Ask **"How many users are affected by DEMO-2026-0001?"** and inspect the answer,
   executed KQL, bound parameters, and trace.
2. Expand **Review the demo reference**, review its definition and KQL, explicitly
   approve it, then select **Compare in Foundry**.

The fixture produces **4 distinct affected users**. The saved flawed baseline
counts **8 finding rows**. A correct query can differ from the reference text and
still pass because the evaluator compares result values.

In Foundry, select the `security-kql` project, then the external agent
`security-kql-demo` and its **Traces** view. The app's **Open agent traces** link
opens this view directly; use it rather than the project-wide **Traces** page.
The evaluation report link appears
after submission. Trace ingestion can take several minutes; the local trace
preview mirrors the same spans immediately. Foundry's custom code evaluator is a
preview feature and requires its feature header.

Agent invocations use internal spans and appear in Application Insights
`dependencies`, matching Foundry's documented external-agent tracing convention.
The Foundry project's managed identity has Reader on the connected Application
Insights resource so the service can retrieve traces. New role assignments can
take up to 10 minutes to propagate. The workspace permits resource-context log
access; a workspace configured to require workspace permissions also needs a
workspace-scoped Log Analytics Reader assignment.

## Scope and controls

- The model selects a structured intent and vulnerability; the backend renders an
  approved KQL template, not arbitrary model-generated code.
- The database executor accepts registered query IDs only, binds parameters,
  requests hardline read-only execution, blocks remote/external access, and sets
  timeout and output limits.
- A query failure or partial database error never becomes an invented answer.
- "Affected users" means distinct nonempty user IDs associated with devices whose
  latest finding state at the fixed snapshot time is Open.
- Repeated scans, duplicate associations, multiple devices per user, reopened and
  resolved findings, unknown users, future observations, and zero matches are
  represented in the fixtures.
- All observations, identities, sensors, and vulnerability identifiers are
  fictional. No production Forescout API is called.
- Reference approval is explicit, local, version-bound demo approval; it is not
  Forescout domain approval or enterprise identity attestation.
- The evaluator grades captured ADX results without network access; it does not
  call the database from the Foundry sandbox.
- This is a fixed-snapshot prototype, not a production-scale performance test,
  general-purpose KQL assistant, or claim of production accuracy.

## Validate

```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\python.exe -m security_kql.cli verify
.\.venv\Scripts\python.exe -m security_kql.cli chat "How many users are affected by DEMO-2026-0001?"
```

With the app running, a real browser check uses installed Microsoft Edge:

```powershell
$env:LIVE_DEMO_URL = 'http://127.0.0.1:8017'
.\.venv\Scripts\pytest.exe -q tests\test_live_browser.py
```

After explicit reference approval, set `$env:LIVE_DEMO_PUBLISH = '1'` to also test
submitting and displaying a real Foundry evaluation through the browser.

The CLI also supports `reference`, `approve-reference --reviewed`, and
`evaluate --publish --wait`. Do not record reference approval without a human's
explicit review.

To pause ADX between demos, stop the dedicated cluster from its Azure portal
page; restart it before the next demo. This does not stop local Python processes.
Keep Azure CLI credentials current with `az login`.
