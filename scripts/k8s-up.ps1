<#
.SYNOPSIS
  Create (or update) the IncidentLab kind cluster and deploy everything:
  metrics-server, ingress-nginx, Argo CD, and the app (deployed by Argo CD from GitHub).
  Stops the Compose stack first: both together do not fit in 8 GB.
  Safe to re-run, including after Docker Desktop restarts: it starts stopped
  nodes and repairs the cluster network if the restart broke it.
  Dashboard: http://127.0.0.1:8090    Argo CD: http://argocd.localhost:8090
#>
param([switch]$SkipBuild)

# docker/kind write progress to stderr, which Windows PowerShell 5.1 would turn
# into terminating errors under "Stop". Failures are caught by exit code (Check).
$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$kind = Join-Path $root ".tools\kind.exe"
if (-not (Test-Path $kind)) { $kind = "kind" }
$cluster = "incidentlab"
$k8s = Join-Path $root "deploy\k8s"
$nodes = @("$cluster-control-plane", "$cluster-worker")

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Check { if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE)" } }

function Wait-ApiServer([int]$seconds = 120) {
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        kubectl get --raw=/readyz *> $null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 3
    } until ((Get-Date) -gt $deadline)
    throw "The Kubernetes API did not become ready in $seconds s."
}

# Each node must have a route to every other node's pod range. After a Docker
# Desktop restart kind has lost these routes, which breaks DNS, webhooks and
# anything that crosses nodes.
function Test-PodRoutes {
    $ok = $true
    # Parse JSON instead of a jsonpath with {"\n"}: Windows PowerShell 5.1 strips
    # the inner quotes when calling kubectl, the jsonpath fails to parse, and an
    # empty list would make this check pass without testing anything.
    $items = (kubectl get nodes -o json | Out-String | ConvertFrom-Json).items
    $cidrs = @($items | Where-Object { $_.spec.podCIDR } |
        ForEach-Object { [pscustomobject]@{ Node = $_.metadata.name; Cidr = $_.spec.podCIDR } })
    if ($cidrs.Count -lt $nodes.Count) {
        Write-Host "  could not read every node's pod range (got $($cidrs.Count) of $($nodes.Count))"
        return $false
    }
    foreach ($node in $nodes) {
        $routes = docker exec $node ip route 2>$null
        foreach ($entry in $cidrs) {
            $other = $entry.Node; $cidr = $entry.Cidr
            if ($other -eq $node) { continue }
            if (-not ($routes | Select-String -SimpleMatch "$cidr via")) {
                Write-Host "  missing route on ${node}: $cidr (pods on $other)"
                $ok = $false
            }
        }
    }
    return $ok
}

# A real lookup from a pod (which runs on the worker, while CoreDNS runs on the
# control plane): catches DNS failures even when the routes look fine.
# Uses the app image already loaded into the nodes, so nothing is downloaded.
# Returns $true (works), $false (broken) or $null (could not test).
function Test-PodDns {
    kubectl delete pod netcheck --ignore-not-found --wait=true *> $null
    $out = kubectl run netcheck --rm -i --restart=Never --quiet `
        --image=incidentlab-app:dev --image-pull-policy=Never --pod-running-timeout=60s `
        --command -- python -c "import socket; socket.gethostbyname('kubernetes.default.svc.cluster.local'); print('dns-ok')" 2>&1
    if ($out -match "dns-ok") { return $true }
    if ($out -match "ErrImageNeverPull|not found|ImagePull") { return $null }
    return $false
}

# Docker can give the node containers different IPs after a restart (they can
# even swap). kind updates the API server's address but not the control-plane
# kubelet's kubeconfig, which keeps the old IP: the node goes NotReady and
# kindnet can't add routes. Point it at the node's hostname instead (always in
# the API server certificate, and resolved by /etc/hosts to the current IP).
function Repair-ControlPlaneKubelet {
    $cp = $nodes[0]
    $server = docker exec $cp grep "server:" /etc/kubernetes/kubelet.conf 2>$null
    if ($server -match "https://(\d+\.\d+\.\d+\.\d+):6443") {
        $current = docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" $cp
        if ($Matches[1] -ne $current) {
            Write-Warning "The control-plane kubelet uses old IP $($Matches[1]) (node is now $current): repointing it to ${cp}."
            docker exec $cp sed -i "s#server: https://[0-9.]*:6443#server: https://${cp}:6443#" /etc/kubernetes/kubelet.conf; Check
            docker exec $cp systemctl restart kubelet; Check
        }
    }
}

# Restarting the network components makes them rebuild routes and connection
# state. A workaround: the root cause of kind losing this state is unknown.
function Repair-ClusterNetwork {
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $routesOk = Test-PodRoutes
        $dns = if ($routesOk) { Test-PodDns } else { $false }
        if ($routesOk -and $dns -ne $false) {
            if ($null -eq $dns) { Write-Warning "Could not run the DNS check (app image not in the nodes yet); routes are fine." }
            else { Write-Host "Cluster network OK (routes + DNS)." }
            return
        }
        Write-Warning "Cluster network is broken (attempt $attempt): restarting kindnet, kube-proxy and CoreDNS..."
        kubectl -n kube-system rollout restart daemonset/kindnet daemonset/kube-proxy deployment/coredns | Out-Null
        kubectl -n kube-system rollout status daemonset/kindnet --timeout=120s | Out-Null
        kubectl -n kube-system rollout status daemonset/kube-proxy --timeout=120s | Out-Null
        kubectl -n kube-system rollout status deployment/coredns --timeout=120s | Out-Null
        # kindnet adds the routes a few seconds after its pod starts.
        $deadline = (Get-Date).AddSeconds(60)
        while (-not (Test-PodRoutes 6> $null) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
    }
    if (-not (Test-PodRoutes) -or (Test-PodDns) -eq $false) {
        throw "The cluster network is still broken. Recreate the cluster: scripts\k8s-down.ps1, then run this script again."
    }
    Write-Host "Cluster network repaired."
}

Step "Checking Docker"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
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
docker compose -p incidentlab stop *> $null

if (-not ((& $kind get clusters) -contains $cluster)) {
    Step "Creating kind cluster '$cluster' (1 control-plane + 1 worker)"
    & $kind create cluster --config (Join-Path $k8s "kind-config.yaml") --wait 120s; Check
} else {
    # After a Docker Desktop restart the node containers may be stopped.
    foreach ($node in $nodes) {
        $state = docker inspect -f "{{.State.Running}}" $node 2>$null
        if ($state -ne "true") {
            Write-Host "Starting stopped node $node"
            docker start $node | Out-Null; Check
        }
    }
}
kubectl config use-context "kind-$cluster" | Out-Null; Check

Step "Waiting for the Kubernetes API"
Wait-ApiServer 120
Write-Host "API ready."
Repair-ControlPlaneKubelet
kubectl wait node --all --for=condition=Ready --timeout=180s; Check

$secrets = Join-Path $k8s "base\secrets.env"
if (-not (Test-Path $secrets)) {
    Copy-Item (Join-Path $k8s "base\secrets.env.example") $secrets
    Write-Warning "Created deploy\k8s\base\secrets.env from the example: change the passwords."
}

if (-not $SkipBuild) {
    Step "Building images"
    $sha = (git -C $root rev-parse --short HEAD 2>$null); if (-not $sha) { $sha = "unknown" }
    docker build -t incidentlab-app:dev --build-arg GIT_SHA=$sha (Join-Path $root "app"); Check
    docker build -t incidentlab-web:dev (Join-Path $root "web"); Check
    docker build -t incidentlab-api:dev (Join-Path $root "lab-api"); Check

    # kind nodes run containerd, not Docker: they can't see local images until loaded.
    Step "Loading images into the kind nodes"
    & $kind load docker-image incidentlab-app:dev incidentlab-web:dev incidentlab-api:dev --name $cluster; Check
}

Step "Checking the cluster network (routes between nodes, DNS)"
Repair-ClusterNetwork

Step "Installing metrics-server (feeds CPU numbers to the HPA)"
kubectl apply -k (Join-Path $k8s "metrics-server"); Check
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s; Check

Step "Installing ingress-nginx (Ingress controller)"
# The controller only runs on nodes with this label (see deploy.yaml).
kubectl label node "$cluster-worker" ingress-ready=true --overwrite | Out-Null; Check
kubectl apply -f (Join-Path $k8s "ingress-nginx\deploy.yaml"); Check
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s; Check
# Its admission webhook validates every Ingress. It only works once these jobs
# have created and installed its certificate.
kubectl -n ingress-nginx wait --for=condition=complete job/ingress-nginx-admission-create job/ingress-nginx-admission-patch --timeout=120s; Check

Step "Installing Argo CD (GitOps)"
# Server-side apply: Argo CD's CRDs are too large for client-side apply's annotation.
kubectl apply --server-side --force-conflicts -k (Join-Path $k8s "argocd\install"); Check
foreach ($d in "argocd-server", "argocd-repo-server", "argocd-redis") {
    kubectl -n argocd rollout status deployment/$d --timeout=300s; Check
}
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s; Check

Step "Creating the app Secret (kept out of Git)"
# Create the namespace only if it's missing: Argo CD owns it (with its Pod
# Security labels), and re-applying a bare namespace would strip those labels.
kubectl get namespace incidentlab *> $null
if ($LASTEXITCODE -ne 0) { kubectl create namespace incidentlab | Out-Null; Check }
kubectl -n incidentlab create secret generic incidentlab-secrets --from-env-file=$secrets --dry-run=client -o yaml | kubectl apply -f - | Out-Null; Check

Step "Handing IncidentLab to Argo CD (it deploys deploy/k8s/base from GitHub)"
kubectl apply -f (Join-Path $k8s "argocd\incidentlab-app.yaml"); Check
# Synced first: an app that failed to build has no resources and still reports
# Healthy. If this times out, see: kubectl -n argocd get application incidentlab -o yaml
kubectl -n argocd wait application/incidentlab --for=jsonpath='{.status.sync.status}'=Synced --timeout=300s; Check
kubectl -n argocd wait application/incidentlab --for=jsonpath='{.status.health.status}'=Healthy --timeout=300s; Check

if (-not $SkipBuild) {
    # Same tag, new image: restart so pods pick up what was just loaded.
    # (The load generator uses the app image too.)
    kubectl -n incidentlab rollout restart deployment incidentlab-app incidentlab-web incidentlab-api incidentlab-loadgen | Out-Null; Check
}

Step "Waiting for workloads"
kubectl -n incidentlab rollout status statefulset/postgres --timeout=180s; Check
foreach ($d in "redis", "incidentlab-app", "incidentlab-web", "incidentlab-api") {
    kubectl -n incidentlab rollout status deployment/$d --timeout=180s; Check
}

Step "Ready"
kubectl -n incidentlab get pods -o wide
Write-Host "`nDashboard: http://127.0.0.1:8090" -ForegroundColor Green
Write-Host "Argo CD:   http://argocd.localhost:8090   (user: admin)" -ForegroundColor Green
Write-Host "Argo CD password (not printed, so it stays out of logs):"
Write-Host '  $pw = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath=''{.data.password}''; [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pw))'
