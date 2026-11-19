<#
.SYNOPSIS
  Create (or update) the MiniCloud kind cluster and deploy everything.
  Stops the Compose stack first: both together do not fit in 8 GB.
  Dashboard: http://127.0.0.1:8090
#>
param([switch]$SkipBuild)

# docker/kind write progress to stderr, which Windows PowerShell 5.1 would turn
# into terminating errors under "Stop". Failures are caught by exit code (Check).
$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$kind = Join-Path $root ".tools\kind.exe"
if (-not (Test-Path $kind)) { $kind = "kind" }
$cluster = "minicloud"
$k8s = Join-Path $root "deploy\k8s"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Check { if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE)" } }

Step "Checking Docker"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { throw "Docker is not running and Docker Desktop was not found. Start Docker first." }
    Write-Host "Docker is not running: starting Docker Desktop and waiting (up to 3 minutes)..."
    Start-Process $desktop
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 5
        docker info *> $null
    } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $deadline)
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not become ready in 3 minutes. Open it, wait for 'Engine running', then run this script again." }
}
Write-Host "Docker is running."

Step "Stopping the Compose stack (if running)"
docker compose -f (Join-Path $root "deploy\compose\compose.yaml") stop *> $null

if (-not ((& $kind get clusters) -contains $cluster)) {
    Step "Creating kind cluster '$cluster' (1 control-plane + 1 worker)"
    & $kind create cluster --config (Join-Path $k8s "kind-config.yaml") --wait 120s; Check
}
kubectl config use-context "kind-$cluster" | Out-Null; Check

$secrets = Join-Path $k8s "base\secrets.env"
if (-not (Test-Path $secrets)) {
    Copy-Item (Join-Path $k8s "base\secrets.env.example") $secrets
    Write-Warning "Created deploy\k8s\base\secrets.env from the example: change the passwords."
}

if (-not $SkipBuild) {
    Step "Building images"
    $sha = (git -C $root rev-parse --short HEAD 2>$null); if (-not $sha) { $sha = "unknown" }
    docker build -t minicloud-app:dev --build-arg GIT_SHA=$sha (Join-Path $root "app"); Check
    docker build -t minicloud-web:dev (Join-Path $root "web"); Check
    docker build -t minicloud-mgmt:dev (Join-Path $root "mgmt-api"); Check

    # kind nodes run containerd, not Docker: they can't see local images until loaded.
    Step "Loading images into the kind nodes"
    & $kind load docker-image minicloud-app:dev minicloud-web:dev minicloud-mgmt:dev --name $cluster; Check
}

Step "Installing metrics-server (feeds CPU numbers to the HPA)"
kubectl apply -k (Join-Path $k8s "metrics-server"); Check

Step "Installing ingress-nginx (Ingress controller)"  
kubectl label node "$cluster-worker" ingress-ready=true --overwrite | Out-Null; Check
kubectl apply -f (Join-Path $k8s "ingress-nginx\deploy.yaml"); Check
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s; Check

Step "Installing Argo CD (GitOps)"
# Server-side apply: Argo CD's CRDs are too large for client-side apply's annotation.
kubectl apply --server-side --force-conflicts -k (Join-Path $k8s "argocd\install"); Check
foreach ($d in "argocd-server", "argocd-repo-server", "argocd-redis") {
    kubectl -n argocd rollout status deployment/$d --timeout=300s; Check
}
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s; Check

Step "Creating the app Secret (kept out of Git)"
kubectl create namespace minicloud --dry-run=client -o yaml | kubectl apply -f - | Out-Null; Check
kubectl -n minicloud create secret generic minicloud-secrets --from-env-file=$secrets --dry-run=client -o yaml | kubectl apply -f - | Out-Null; Check

Step "Handing MiniCloud to Argo CD (it deploys deploy/k8s/base from GitHub)"
kubectl apply -f (Join-Path $k8s "argocd\minicloud-app.yaml"); Check
kubectl -n argocd wait application/minicloud --for=jsonpath='{.status.health.status}'=Healthy --timeout=300s; Check


if (-not $SkipBuild) {
    # Same tag, new image: restart so pods pick up what was just loaded.
    kubectl -n minicloud rollout restart deployment minicloud-app minicloud-web minicloud-mgmt-api | Out-Null
}

Step "Waiting for workloads"
kubectl -n minicloud rollout status statefulset/postgres --timeout=180s; Check
foreach ($d in "redis", "minicloud-app", "minicloud-web", "minicloud-mgmt-api") {
    kubectl -n minicloud rollout status deployment/$d --timeout=180s; Check
}
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s; Check

Step "Ready"
kubectl -n minicloud get pods -o wide
Write-Host "`nDashboard: http://127.0.0.1:8090" -ForegroundColor Green
Write-Host "Argo CD:   http://argocd.localhost:8090   (user: admin)" -ForegroundColor Green
$pw = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}'
if ($pw) { Write-Host ("Argo CD password: " + [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pw))) }
