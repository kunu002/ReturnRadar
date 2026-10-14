#!/usr/bin/env bash
# Run manually after review. No command pushes Git or creates repository triggers.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
PYTHON=${DEPLOY_PYTHON:-python3}
if [[ -x "$ROOT/.venv/bin/python" && -z "${DEPLOY_PYTHON:-}" ]]; then PYTHON="$ROOT/.venv/bin/python"; fi
COMMAND=${1:-check}
CONFIG=${DEPLOY_CONFIG:-infra/deploy.env}

fail() { printf '%s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || fail "Install $1 first. See docs/DEPLOYMENT.md."; }
gc() { gcloud --project="$PROJECT_ID" "$@"; }

load_config() {
  [[ -f "$CONFIG" ]] || fail "Copy infra/deploy.env.example to infra/deploy.env and fill in the public project settings."
  # Parse plain KEY=value pairs. Never execute this file or source local .env.
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Z_]+)=([a-zA-Z0-9_.:/,@+-]*)$ ]] || fail "Use plain KEY=value entries in $CONFIG, without shell expressions or quotes."
    key=${BASH_REMATCH[1]}; value=${BASH_REMATCH[2]}
    case "$key" in
      PROJECT_ID|REGION|FIRESTORE_LOCATION|REPOSITORY|API_SERVICE|WORKER_SERVICE|QUEUE|GEMINI_MODEL|FIREBASE_WEB_API_KEY|FIREBASE_AUTH_DOMAIN|FIREBASE_SUPPORT_EMAIL|CORS_ORIGINS|IMAGE) export "$key=$value" ;;
      *) fail "Unsupported deployment setting: $key" ;;
    esac
  done < "$CONFIG"
  : "${PROJECT_ID:?Set PROJECT_ID in infra/deploy.env}"
  [[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ && "$PROJECT_ID" != *YOUR* && "$PROJECT_ID" != *your-project* ]] || fail "Set your actual Google Cloud project ID."
  REGION=${REGION:-asia-south1}; FIRESTORE_LOCATION=${FIRESTORE_LOCATION:-$REGION}
  REPOSITORY=${REPOSITORY:-returnradar}; API_SERVICE=${API_SERVICE:-returnradar-api}
  WORKER_SERVICE=${WORKER_SERVICE:-returnradar-worker}; QUEUE=${QUEUE:-returnradar-work}
  GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.1-flash-lite}
  FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN:-$PROJECT_ID.web.app}
  CORS_ORIGINS=${CORS_ORIGINS:-https://$PROJECT_ID.web.app,https://$PROJECT_ID.firebaseapp.com}
  for name in "$REGION" "$FIRESTORE_LOCATION" "$REPOSITORY" "$API_SERVICE" "$WORKER_SERVICE" "$QUEUE"; do
    [[ "$name" =~ ^[a-z][a-z0-9-]+$ ]] || fail "Use lowercase Google resource names."
  done
  [[ "$API_SERVICE" != "$WORKER_SERVICE" ]] || fail "The public API and private worker must have different service names."
  [[ "$FIREBASE_AUTH_DOMAIN" =~ ^[a-z0-9][a-z0-9.-]+[a-z0-9]$ ]] || fail "FIREBASE_AUTH_DOMAIN must be a hostname."
  [[ "$GEMINI_MODEL" =~ ^[a-zA-Z0-9._-]+$ ]] || fail "GEMINI_MODEL must be a model identifier."
  DEPLOY_ORIGINS="$CORS_ORIGINS" "$PYTHON" -c 'import os,sys; from urllib.parse import urlsplit; urls=os.environ["DEPLOY_ORIGINS"].split(","); valid=bool(urls) and all((lambda u:u.scheme=="https" and bool(u.hostname) and "." in u.hostname and u.hostname!="localhost" and not u.path and u.port in (None,443) and not u.username and not u.password and not u.query and not u.fragment)(urlsplit(s)) for s in urls); sys.exit(0 if valid else "CORS_ORIGINS must contain exact HTTPS origins without paths or trailing slashes.")'
  API_SA="returnradar-api@$PROJECT_ID.iam.gserviceaccount.com"
  WORKER_SA="returnradar-worker@$PROJECT_ID.iam.gserviceaccount.com"
  TASKS_SA="returnradar-tasks@$PROJECT_ID.iam.gserviceaccount.com"
  BUILD_SA="returnradar-build@$PROJECT_ID.iam.gserviceaccount.com"
  SOURCE_BUCKET="$PROJECT_ID-returnradar-build-source"
  mkdir -p infra/generated
}

create_sa() {
  local identifier=$1
  if ! gc iam service-accounts describe "$identifier@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gc iam service-accounts create "$identifier" --display-name="$identifier"
  fi
}

bootstrap() {
  gc services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
    firestore.googleapis.com cloudtasks.googleapis.com secretmanager.googleapis.com \
    iam.googleapis.com iamcredentials.googleapis.com logging.googleapis.com storage.googleapis.com \
    identitytoolkit.googleapis.com firebase.googleapis.com firebasehosting.googleapis.com
  gc beta services identity create --service=cloudtasks.googleapis.com --quiet >/dev/null
  for identifier in returnradar-api returnradar-worker returnradar-tasks returnradar-build; do create_sa "$identifier"; done
  if ! gc artifacts repositories describe "$REPOSITORY" --location="$REGION" >/dev/null 2>&1; then
    gc artifacts repositories create "$REPOSITORY" --location="$REGION" --repository-format=docker --description="ReturnRadar runtime images"
  fi
  if ! gc firestore databases describe --database='(default)' >/dev/null 2>&1; then
    gc firestore databases create --database='(default)' --location="$FIRESTORE_LOCATION" --type=firestore-native --delete-protection
  fi
  gc firestore databases update --database='(default)' --delete-protection --enable-pitr
  if ! gc tasks queues describe "$QUEUE" --location="$REGION" >/dev/null 2>&1; then
    gc tasks queues create "$QUEUE" --location="$REGION"
  fi
  gc tasks queues update "$QUEUE" --location="$REGION" --max-attempts=5 --min-backoff=300s --max-backoff=300s \
    --max-concurrent-dispatches=1 --max-dispatches-per-second=1 --log-sampling-ratio=0.1
  for identity in "$API_SA" "$WORKER_SA"; do
    gc projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$identity" --role=roles/datastore.user --condition=None >/dev/null
  done
  gc projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$API_SA" --role=roles/firebaseauth.viewer --condition=None >/dev/null
  gc tasks queues add-iam-policy-binding "$QUEUE" --location="$REGION" --member="serviceAccount:$API_SA" --role=roles/cloudtasks.enqueuer >/dev/null
  gc iam service-accounts add-iam-policy-binding "$TASKS_SA" --member="serviceAccount:$API_SA" --role=roles/iam.serviceAccountUser >/dev/null
  project_number=$(gc projects describe "$PROJECT_ID" --format='value(projectNumber)')
  # The service agent signs OIDC tokens only for this dedicated task identity.
  gc iam service-accounts add-iam-policy-binding "$TASKS_SA" --member="serviceAccount:service-$project_number@gcp-sa-cloudtasks.iam.gserviceaccount.com" --role=roles/iam.serviceAccountTokenCreator >/dev/null
  if ! gc storage buckets describe "gs://$SOURCE_BUCKET" >/dev/null 2>&1; then
    gc storage buckets create "gs://$SOURCE_BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  fi
  gc storage buckets add-iam-policy-binding "gs://$SOURCE_BUCKET" --member="serviceAccount:$BUILD_SA" --role=roles/storage.objectViewer >/dev/null
  gc artifacts repositories add-iam-policy-binding "$REPOSITORY" --location="$REGION" --member="serviceAccount:$BUILD_SA" --role=roles/artifactregistry.writer >/dev/null
  gc projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$BUILD_SA" --role=roles/logging.logWriter --condition=None >/dev/null
  for secret in returnradar-gemini-api-key returnradar-connector-key; do
    if ! gc secrets describe "$secret" >/dev/null 2>&1; then gc secrets create "$secret" --replication-policy=automatic; fi
    for identity in "$API_SA" "$WORKER_SA"; do
      gc secrets add-iam-policy-binding "$secret" --member="serviceAccount:$identity" --role=roles/secretmanager.secretAccessor >/dev/null
    done
  done
  printf '%s\n' "Resources prepared. Enable Firebase Google sign-in and initialize secrets next; see docs/DEPLOYMENT.md."
}

enabled_version() {
  gc secrets versions list "$1" --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)' | awk -F/ '{print $NF}'
}

initialize_secrets() {
  umask 077
  secret_file=$(mktemp)
  trap 'rm -f "$secret_file"' EXIT
  if [[ -z "$(enabled_version returnradar-gemini-api-key)" ]]; then
    [[ -t 0 ]] || fail "Run secrets interactively so the Gemini key is not supplied in command arguments."
    IFS= read -r -s -p "Gemini API key (hidden input): " gemini_key
    printf '\n'
    [[ -n "$gemini_key" ]] || fail "The Gemini key cannot be empty."
    printf '%s' "$gemini_key" > "$secret_file"
    unset gemini_key
    gc secrets versions add returnradar-gemini-api-key --data-file="$secret_file" >/dev/null
  fi
  if [[ -z "$(enabled_version returnradar-connector-key)" ]]; then
    "$PYTHON" -c 'import base64,secrets,sys; sys.stdout.buffer.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))' > "$secret_file"
    gc secrets versions add returnradar-connector-key --data-file="$secret_file" >/dev/null
  fi
  printf '%s\n' "Secret versions are ready. Existing versions were preserved; values were not printed."
}

build_image() {
  "$PYTHON" scripts/check-deployment.py
  build_id=$(gc builds submit . --config=cloudbuild.yaml --ignore-file=.gcloudignore --region="$REGION" \
    --service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
    --gcs-source-staging-dir="gs://$SOURCE_BUCKET/source" \
    --substitutions="_REGION=$REGION,_REPOSITORY=$REPOSITORY" --suppress-logs --format='value(id)')
  [[ "$build_id" =~ ^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$ ]] || fail "Cloud Build did not return a valid build ID. Review Cloud Build logs."
  image_tag="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/returnradar:$build_id"
  digest=$(gc artifacts docker images describe "$image_tag" --format='value(image_summary.digest)')
  [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail "Could not resolve the built image digest."
  printf '%s\n' "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/returnradar@$digest" > infra/generated/image.txt
  printf '%s\n' "Validated container saved as an immutable digest in infra/generated/image.txt. Deploy it after reviewing the build."
}

deploy_services() {
  [[ -n "${FIREBASE_WEB_API_KEY:-}" && "$FIREBASE_WEB_API_KEY" != *YOUR* ]] || fail "Fill in FIREBASE_WEB_API_KEY from your Firebase web app."
  if [[ -z "${IMAGE:-}" && -f infra/generated/image.txt ]]; then IMAGE=$(cat infra/generated/image.txt); fi
  [[ "${IMAGE:-}" =~ ^[a-z0-9-]+-docker\.pkg\.dev/[a-z0-9-]+/[a-z0-9-]+/returnradar@sha256:[a-f0-9]{64}$ ]] || fail "Run build first or set IMAGE to an immutable Artifact Registry digest."
  [[ "$IMAGE" == "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/returnradar@sha256:"* ]] || fail "The image must belong to this configured project, region, and repository."
  gemini_version=$(enabled_version returnradar-gemini-api-key)
  connector_version=$(enabled_version returnradar-connector-key)
  [[ "$gemini_version" =~ ^[0-9]+$ && "$connector_version" =~ ^[0-9]+$ ]] || fail "Initialize secrets before deployment."
  export PROJECT_ID GEMINI_MODEL FIREBASE_WEB_API_KEY FIREBASE_AUTH_DOMAIN CORS_ORIGINS REGION QUEUE TASKS_SA
  "$PYTHON" - <<'PY'
import json,os
from pathlib import Path
common={"DEMO_MODE":"false","AUTH_PROVIDER":"firebase","STORAGE_BACKEND":"firestore","GOOGLE_CLOUD_PROJECT":os.environ["PROJECT_ID"],"GEMINI_MODEL":os.environ["GEMINI_MODEL"]}
Path("infra/generated/worker-env.yaml").write_text(json.dumps({**common,"SERVICE_ROLE":"worker"}))
PY
  secret_mapping="GEMINI_API_KEY=returnradar-gemini-api-key:$gemini_version,CONNECTOR_ENCRYPTION_KEY=returnradar-connector-key:$connector_version"
  gc run deploy "$WORKER_SERVICE" --image="$IMAGE" --region="$REGION" --service-account="$WORKER_SA" \
    --no-allow-unauthenticated --ingress=all --env-vars-file=infra/generated/worker-env.yaml --set-secrets="$secret_mapping" \
    --port=8080 --cpu=1 --memory=2Gi --concurrency=1 --timeout=1800 --min-instances=0 --max-instances=1 \
    --startup-probe='httpGet.path=/api/ready,httpGet.port=8080,periodSeconds=10,timeoutSeconds=5,failureThreshold=12'
  gc run services add-iam-policy-binding "$WORKER_SERVICE" --region="$REGION" --member="serviceAccount:$TASKS_SA" --role=roles/run.invoker >/dev/null
  WORKER_URL=$(gc run services describe "$WORKER_SERVICE" --region="$REGION" --format='value(status.url)')
  export WORKER_URL
  "$PYTHON" - <<'PY'
import json,os
from pathlib import Path
env=json.loads(Path("infra/generated/worker-env.yaml").read_text())
env.update({"SERVICE_ROLE":"api","FIREBASE_WEB_API_KEY":os.environ["FIREBASE_WEB_API_KEY"],"FIREBASE_AUTH_DOMAIN":os.environ["FIREBASE_AUTH_DOMAIN"],"CORS_ORIGINS":os.environ["CORS_ORIGINS"],"CLOUD_TASKS_LOCATION":os.environ["REGION"],"CLOUD_TASKS_QUEUE":os.environ["QUEUE"],"WORKER_URL":os.environ["WORKER_URL"],"TASKS_SERVICE_ACCOUNT":os.environ["TASKS_SA"]})
Path("infra/generated/api-env.yaml").write_text(json.dumps(env))
PY
  gc run deploy "$API_SERVICE" --image="$IMAGE" --region="$REGION" --service-account="$API_SA" \
    --allow-unauthenticated --ingress=all --env-vars-file=infra/generated/api-env.yaml --set-secrets="$secret_mapping" \
    --port=8080 --cpu=1 --memory=1Gi --concurrency=20 --timeout=60 --min-instances=0 --max-instances=1 \
    --startup-probe='httpGet.path=/api/ready,httpGet.port=8080,periodSeconds=10,timeoutSeconds=5,failureThreshold=12'
  printf '%s\n' "Cloud Run services deployed. The API requires Firebase sign-in for merchant data; the worker requires IAM authentication. Publish Hosting next."
}

firebase_config() {
  export API_SERVICE REGION PROJECT_ID FIREBASE_AUTH_DOMAIN
  "$PYTHON" - "$1" "$2" <<'PY'
import json,os,sys
from pathlib import Path
config=json.loads(Path("firebase.json").read_text())
config["hosting"]["rewrites"][0]["run"].update(serviceId=os.environ["API_SERVICE"],region=os.environ["REGION"])
if sys.argv[2]=="auth":
    default_domain=os.environ["PROJECT_ID"]+".firebaseapp.com"
    # Firebase provisioning adds its standard firebaseapp.com callback itself.
    domains=[domain for domain in dict.fromkeys([os.environ["PROJECT_ID"]+".web.app",os.environ["FIREBASE_AUTH_DOMAIN"]]) if domain!=default_domain]
    # Include only Google so this command leaves other provider settings untouched.
    config["auth"]={"providers":{"googleSignIn":{
        "oAuthBrandDisplayName":"ReturnRadar",
        "supportEmail":os.environ["FIREBASE_SUPPORT_EMAIL"],
        "authorizedRedirectUris":["https://"+domain+"/__/auth/handler" for domain in domains],
    }}}
Path(sys.argv[1]).write_text(json.dumps(config,indent=2)+"\n")
PY
}

configure_auth() {
  need firebase
  [[ "${FIREBASE_SUPPORT_EMAIL:-}" =~ ^[a-zA-Z0-9._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ && "$FIREBASE_SUPPORT_EMAIL" != *YOUR* ]] || fail "Set FIREBASE_SUPPORT_EMAIL to an authorized project member's email in infra/deploy.env."
  export FIREBASE_SUPPORT_EMAIL
  firebase_config infra/generated/firebase-auth.json auth
  # Firebase CLI ADC requests need an explicit quota project. Keep debug logs off.
  GOOGLE_CLOUD_QUOTA_PROJECT="$PROJECT_ID" DEBUG= firebase deploy --project="$PROJECT_ID" --config=infra/generated/firebase-auth.json --only auth
  printf '%s\n' "Google sign-in configuration deployed. Verify authorized domains in Firebase Authentication before publishing Hosting."
}

deploy_hosting() {
  need firebase; need npm
  VITE_API_URL='' npm ci
  VITE_API_URL='' npm run build
  firebase_config .firebase.deploy.json hosting
  GOOGLE_CLOUD_QUOTA_PROJECT="$PROJECT_ID" DEBUG= firebase deploy --project="$PROJECT_ID" --config=.firebase.deploy.json --only hosting,firestore:rules
  printf '%s\n' "Open https://$PROJECT_ID.web.app and finish the merchant smoke test in docs/DEPLOYMENT.md."
}

verify_deployment() {
  export PROJECT_ID WORKER_URL
  WORKER_URL=$(gc run services describe "$WORKER_SERVICE" --region="$REGION" --format='value(status.url)')
  export WORKER_URL
  "$PYTHON" - <<'PY'
import json,os,ssl,urllib.request,urllib.error
import certifi
ssl_context=ssl.create_default_context()
ssl_context.load_verify_locations(cafile=certifi.where())
base="https://"+os.environ["PROJECT_ID"]+".web.app"
def get(url):
    try:
        with urllib.request.urlopen(url,timeout=30,context=ssl_context) as response:return response.status,response.read()
    except urllib.error.HTTPError as error:return error.code,b""
for path in ["/", "/api/health", "/api/ready"]:
    status,_=get(base+path)
    if status!=200:raise SystemExit(f"Smoke check failed: {path} returned {status}.")
status,payload=get(base+"/api/config")
config=json.loads(payload) if status==200 else {}
if config.get("demo") is not False or config.get("auth_provider")!="firebase":raise SystemExit("Production auth configuration check failed.")
for path in ["/api/dashboard", "/api/internal/process"]:
    status,_=get(base+path)
    if status not in [401,403,404,405]:raise SystemExit(f"Smoke check failed: {path} is unexpectedly accessible ({status}).")
status,_=get(os.environ["WORKER_URL"]+"/api/health")
if status not in [401,403]:raise SystemExit(f"Worker must reject unauthenticated requests; received {status}.")
print("Public routes, production auth, and private worker smoke checks passed.")
PY
}

case "$COMMAND" in
  check) "$PYTHON" scripts/check-deployment.py; bash -n scripts/deploy-cloud.sh ;;
  bootstrap|secrets|build|deploy|auth|hosting|verify)
    need gcloud; load_config
    case "$COMMAND" in
      bootstrap) bootstrap ;;
      secrets) initialize_secrets ;;
      build) build_image ;;
      deploy) deploy_services ;;
      auth) configure_auth ;;
      hosting) deploy_hosting ;;
      verify) verify_deployment ;;
    esac ;;
  *) fail "Usage: bash scripts/deploy-cloud.sh {check|bootstrap|secrets|build|deploy|auth|hosting|verify}" ;;
esac
