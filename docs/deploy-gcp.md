# Deploying Agendable on GCP (Cloud Run + Cloud SQL)

This guide deploys Agendable to Google Cloud Platform using:

- Cloud Run service for the web app
- Cloud Run Job for reminder processing (triggered by Cloud Scheduler)
- Cloud SQL (Postgres)
- Secret Manager for app secrets
- Artifact Registry for container images

It is intentionally minimal and production-oriented.

## 1) Prerequisites

- A GCP project with billing enabled
- `gcloud` CLI installed and authenticated
- Docker available locally (or use Cloud Build)
- Domain name (optional at first, recommended before external users)

Set variables used in commands below:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export REPO="agendable"
export IMAGE="agendable"
export DB_INSTANCE="agendable-pg"
export DB_NAME="agendable"
export DB_USER="agendable"
export WEB_SERVICE="agendable-web"
export REMINDER_JOB="agendable-reminders"
export SCHEDULER_JOB="agendable-reminders-every-minute"
```

Then:

```bash
gcloud config set project "$PROJECT_ID"
```

## 2) Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com
```

## 3) Create Artifact Registry repo

```bash
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Agendable container images"
```

## 4) Create Cloud SQL Postgres instance + database/user

```bash
gcloud sql instances create "$DB_INSTANCE" \
  --database-version=POSTGRES_16 \
  --cpu=1 \
  --memory=3840MB \
  --region="$REGION"

gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE"

gcloud sql users create "$DB_USER" --instance="$DB_INSTANCE" --password="CHANGE_ME_STRONG_PASSWORD"
```

Build async SQLAlchemy URL (used by app):

```text
postgresql+asyncpg://DB_USER:DB_PASSWORD@/DB_NAME?host=/cloudsql/PROJECT_ID:REGION:DB_INSTANCE
```

## 5) Create Secret Manager secrets

Create one-time values:

- `AGENDABLE_DATABASE_URL`
- `AGENDABLE_SESSION_SECRET`
- `AGENDABLE_OIDC_CLIENT_SECRET` (if using OIDC)
- `AGENDABLE_SMTP_PASSWORD` (if SMTP auth is used)

Example:

```bash
printf '%s' 'postgresql+asyncpg://agendable:CHANGE_ME_STRONG_PASSWORD@/agendable?host=/cloudsql/'"$PROJECT_ID:$REGION:$DB_INSTANCE" | \
  gcloud secrets create AGENDABLE_DATABASE_URL --data-file=-

openssl rand -hex 32 | gcloud secrets create AGENDABLE_SESSION_SECRET --data-file=-
```

For secret updates later:

```bash
printf '%s' 'new-value' | gcloud secrets versions add SECRET_NAME --data-file=-
```

## 6) Create runtime service account

```bash
gcloud iam service-accounts create agendable-runtime \
  --display-name="Agendable runtime"

export RUNTIME_SA="agendable-runtime@$PROJECT_ID.iam.gserviceaccount.com"
```

Grant it access to read secrets:

```bash
for SECRET in AGENDABLE_DATABASE_URL AGENDABLE_SESSION_SECRET AGENDABLE_OIDC_CLIENT_SECRET AGENDABLE_SMTP_PASSWORD; do
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/secretmanager.secretAccessor" || true
done
```

## 7) Build and push the image

```bash
export IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE:$(git rev-parse --short HEAD)"

gcloud builds submit --tag "$IMAGE_URI"
```

## 8) Run migrations (Cloud Run Job)

Create a one-off migration job and execute it on each release before web rollout:

```bash
gcloud run jobs create agendable-migrate \
  --image "$IMAGE_URI" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --add-cloudsql-instances "$PROJECT_ID:$REGION:$DB_INSTANCE" \
  --set-env-vars AGENDABLE_AUTO_CREATE_DB=false \
  --set-secrets AGENDABLE_DATABASE_URL=AGENDABLE_DATABASE_URL:latest \
  --command alembic \
  --args upgrade,head || true

gcloud run jobs update agendable-migrate \
  --image "$IMAGE_URI" \
  --region "$REGION"

gcloud run jobs execute agendable-migrate --region "$REGION" --wait
```

## 9) Deploy web service (Cloud Run)

Set non-secret env vars appropriate for production:

- `AGENDABLE_AUTO_CREATE_DB=false`
- `AGENDABLE_LOG_JSON=true`
- `AGENDABLE_LOG_LEVEL=INFO`
- `AGENDABLE_ALLOWED_EMAIL_DOMAIN` (optional)
- `AGENDABLE_OIDC_CLIENT_ID` and `AGENDABLE_OIDC_METADATA_URL` (if using OIDC)

Deploy:

```bash
gcloud run deploy "$WEB_SERVICE" \
  --image "$IMAGE_URI" \
  --region "$REGION" \
  --allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --add-cloudsql-instances "$PROJECT_ID:$REGION:$DB_INSTANCE" \
  --set-env-vars AGENDABLE_AUTO_CREATE_DB=false,AGENDABLE_LOG_JSON=true,AGENDABLE_LOG_LEVEL=INFO \
  --set-secrets AGENDABLE_DATABASE_URL=AGENDABLE_DATABASE_URL:latest,AGENDABLE_SESSION_SECRET=AGENDABLE_SESSION_SECRET:latest
```

If using OIDC/SMTP secrets, add them in `--set-secrets` and related non-secret vars in `--set-env-vars`.

## 10) Deploy reminder worker as Cloud Run Job

Rather than a continuously polling worker, run reminders on a schedule:

```bash
gcloud run jobs create "$REMINDER_JOB" \
  --image "$IMAGE_URI" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --add-cloudsql-instances "$PROJECT_ID:$REGION:$DB_INSTANCE" \
  --set-env-vars AGENDABLE_AUTO_CREATE_DB=false,AGENDABLE_LOG_JSON=true,AGENDABLE_LOG_LEVEL=INFO \
  --set-secrets AGENDABLE_DATABASE_URL=AGENDABLE_DATABASE_URL:latest,AGENDABLE_SESSION_SECRET=AGENDABLE_SESSION_SECRET:latest \
  --command agendable \
  --args run-reminders || true

gcloud run jobs update "$REMINDER_JOB" \
  --image "$IMAGE_URI" \
  --region "$REGION"
```

## 11) Schedule reminders with Cloud Scheduler

Run every minute:

```bash
gcloud scheduler jobs create http "$SCHEDULER_JOB" \
  --location "$REGION" \
  --schedule "* * * * *" \
  --uri "https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$REMINDER_JOB:run" \
  --http-method POST \
  --oauth-service-account-email "$RUNTIME_SA" || true
```

If it already exists, update it:

```bash
gcloud scheduler jobs update http "$SCHEDULER_JOB" \
  --location "$REGION" \
  --schedule "* * * * *" \
  --uri "https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$REMINDER_JOB:run" \
  --http-method POST \
  --oauth-service-account-email "$RUNTIME_SA"
```

## 12) OIDC callback URL update

After first web deploy, capture service URL:

```bash
gcloud run services describe "$WEB_SERVICE" --region "$REGION" --format='value(status.url)'
```

Set your OIDC callback in provider config to:

```text
https://<cloud-run-url>/auth/oidc/callback
```

If using a custom domain, switch to your domain callback URL.

## 13) Suggested production hardening

- Use Cloud Run min instances `>=1` if you want lower cold-start latency.
- Restrict Cloud Run ingress if fronting with a load balancer.
- Configure custom domain + managed TLS cert.
- Create alerting on Cloud Run error rate and Cloud SQL CPU/storage.
- Enable automated Cloud SQL backups and test restore in staging.

## 14) Release procedure (recommended)

For every release:

1. Build/push image
2. Update + execute migration job
3. Deploy web service with new image
4. Update reminder job image
5. Smoke test login, OIDC callback, create task, reminder run

## 15) Rollback

Web rollback:

```bash
gcloud run revisions list --service "$WEB_SERVICE" --region "$REGION"
gcloud run services update-traffic "$WEB_SERVICE" --region "$REGION" --to-revisions <previous-revision>=100
```

Reminder job rollback:

```bash
gcloud run jobs update "$REMINDER_JOB" --image "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE:<previous-tag>" --region "$REGION"
```

## 16) GitHub Actions deployment (optional)

This repo includes `.github/workflows/deploy-gcp.yml` for manual production deploys.

### Trigger

- GitHub Actions → **Deploy GCP** → **Run workflow**

### Required GitHub repository variables

- `GCP_PROJECT_ID` (example: `my-prod-project`)
- `GCP_REGION` (example: `us-central1`)
- `GCP_ARTIFACT_REPO` (example: `agendable`)
- `GCP_IMAGE_NAME` (example: `agendable`)
- `GCP_CLOUDSQL_INSTANCE` (format: `PROJECT:REGION:INSTANCE`, example: `my-prod-project:us-central1:agendable-pg-prod`)
- `GCP_WEB_SERVICE` (example: `agendable-web`)
- `GCP_REMINDER_JOB` (example: `agendable-reminders`)
- `GCP_SCHEDULER_JOB` (example: `agendable-reminders-every-minute`)
- `GCP_RUNTIME_SERVICE_ACCOUNT` (example: `agendable-runtime@my-prod-project.iam.gserviceaccount.com`)

### Required GitHub repository secrets

- `GCP_WORKLOAD_IDENTITY_PROVIDER` (full provider resource name)
- `GCP_DEPLOY_SERVICE_ACCOUNT` (deployer service account email)

### Required GCP Secret Manager secret names

The workflow expects these existing secret names in GCP:

- `AGENDABLE_DATABASE_URL`
- `AGENDABLE_SESSION_SECRET`

### One-time IAM setup for GitHub OIDC (Workload Identity Federation)

The deployer service account must allow token exchange from your GitHub repo identity and have permissions to:

- build/push images (`Cloud Build`, `Artifact Registry` write)
- deploy Cloud Run services/jobs
- execute Cloud Run jobs
- read Secret Manager secrets
- connect Cloud SQL instances

The runtime service account also needs permission to run the reminder Cloud Run Job when used by Cloud Scheduler OAuth calls.
Grant at least a role containing `run.jobs.run` on the reminder job (for example `roles/run.developer` scoped narrowly to the job/project).

Recommended approach: create a dedicated deployer service account and grant least-privilege roles only for deployment tasks.
