[CmdletBinding()]
param(
    [ValidatePattern('^127\.0\.0\.1$')]
    [string]$ApiHost = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8021,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]
    [string]$Model = 'qwen2.5:3b',
    [ValidatePattern('^ekaa-stage21-[a-z0-9][a-z0-9_-]{0,40}$')]
    [string]$ProjectName = 'ekaa-stage21-demo',
    [string]$Question = 'What is the synthetic demo recovery target?'
)

$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:EnvFile = Join-Path $script:RepoRoot '.env.stage21'
$script:Fixture = Join-Path $script:RepoRoot 'docs/fixtures/stage21_synthetic_recovery.md'
$script:ComposeFiles = @(
    (Join-Path $script:RepoRoot 'compose.yaml'),
    (Join-Path $script:RepoRoot 'compose.stage21.yaml')
)
$script:ComposePrefix = @('compose', '--env-file', $script:EnvFile)
foreach ($composeFile in $script:ComposeFiles) {
    $script:ComposePrefix += @('-f', $composeFile)
}
$script:ComposePrefix += @('--project-name', $ProjectName)

function Stop-Safely([string]$Message) {
    Write-Error $Message
    exit 1
}

function Invoke-ComposeStep([string[]]$Arguments, [string]$Step) {
    $null = & docker @script:ComposePrefix @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed during '$Step'. Check Docker Desktop and the Stage 21 local configuration; command output was suppressed."
    }
}

function Invoke-ComposeCount([string[]]$Arguments, [string]$Step) {
    $output = & docker @script:ComposePrefix @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed during '$Step'. Check the isolated Stage 21 database; command output was suppressed."
    }
    return (($output -join '').Trim())
}

function Test-RequiredEnvKeys {
    $required = @('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')
    $found = @{}
    foreach ($line in Get-Content -LiteralPath $script:EnvFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $found[$Matches[1]] = $true
        }
    }
    foreach ($key in $required) {
        if (-not $found.ContainsKey($key)) {
            throw "The .env.stage21 file is missing required key '$key'. Copy .env.stage21.example and fill its local-only values."
        }
    }
}

if ($ApiHost -ne '127.0.0.1') {
    Stop-Safely 'The demo API host must be 127.0.0.1, matching the Compose loopback port binding.'
}
$parsedHost = [System.Net.IPAddress]::Parse($ApiHost)
if ($parsedHost.GetAddressBytes()[0] -ne 127) {
    Stop-Safely 'The demo API host must be an IPv4 loopback address.'
}
if (-not (Test-Path -LiteralPath $script:EnvFile -PathType Leaf)) {
    Stop-Safely 'Missing .env.stage21. Copy .env.stage21.example to .env.stage21, then set local-only database values.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Safely 'Docker CLI was not found. Start Docker Desktop and verify that docker is on PATH.'
}
if (-not (Test-Path -LiteralPath $script:Fixture -PathType Leaf)) {
    Stop-Safely 'The checked-in synthetic Stage 21 fixture is missing.'
}

$oldApiPort = $env:EKAA_API_PORT
$oldModel = $env:EKAA_LLM_MODEL
try {
    Test-RequiredEnvKeys
    $env:EKAA_API_PORT = [string]$ApiPort
    $env:EKAA_LLM_MODEL = $Model

    $tags = $null
    try {
        $tags = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
    }
    catch {
        throw 'Ollama is not responding on 127.0.0.1:11434. Start Ollama and ensure the selected model is installed.'
    }
    $installedModels = @($tags.models | ForEach-Object { $_.name })
    if ($installedModels -notcontains $Model) {
        throw "Ollama does not report model '$Model'. Pull or select an installed model using -Model."
    }

    Write-Host "Starting isolated Stage 21 services under Compose project '$ProjectName'..."
    Invoke-ComposeStep -Arguments @('up', '-d', 'db') -Step 'start PostgreSQL'
    Invoke-ComposeStep -Arguments @('up', '-d', '--build', '--force-recreate', 'api') -Step 'recreate Stage 21 API configuration'

    $apiBase = "http://${ApiHost}:${ApiPort}"
    $apiReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "$apiBase/health" -TimeoutSec 3
            if ($health.status -eq 'ok') {
                $apiReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $apiReady) {
        throw 'The local EKAA API did not become healthy. Check Docker Desktop and the API container health/logs without sharing secret values.'
    }

    Invoke-ComposeStep -Arguments @(
        'exec', '-T', 'db', 'sh', '-c',
        'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    ) -Step 'check PostgreSQL readiness'

    $uploadJson = & curl.exe --silent --show-error --fail `
        --form "file=@$script:Fixture;type=text/markdown" `
        "$apiBase/api/documents" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Synthetic fixture upload failed. Check the API health and local database readiness.'
    }
    $upload = $uploadJson | ConvertFrom-Json
    if (-not $upload.document_id) {
        throw 'The upload response did not contain a document ID.'
    }

    Invoke-ComposeStep -Arguments @('exec', '-T', 'api', 'python', '-m', 'backend.app.embeddings.cli', 'embed-pending') -Step 'embed pending fixture chunks'

    $coverageSql = 'SELECT (SELECT count(*) FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE d.status = ''completed'') || ''|'' || (SELECT count(DISTINCT e.chunk_id) FROM chunk_embeddings e JOIN document_chunks c ON c.id = e.chunk_id JOIN documents d ON d.id = c.document_id WHERE d.status = ''completed'' AND e.embedding_model = ''deterministic-hash-v2'' AND e.embedding_dimensions = 64);'
    $coverageCommand = "psql -X -qAt -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -c `"$coverageSql`""
    $coverage = Invoke-ComposeCount -Arguments @('exec', '-T', 'db', 'sh', '-c', $coverageCommand) -Step 'verify v2 embedding coverage'
    if ($coverage -notmatch '^(\d+)\|(\d+)$') {
        throw 'The isolated database did not return valid completed-chunk embedding counts.'
    }
    $completedChunkCount = [int]$Matches[1]
    $v2ChunkCount = [int]$Matches[2]
    if ($completedChunkCount -lt 1 -or $completedChunkCount -ne $v2ChunkCount) {
        throw 'Version 2 embeddings do not cover every completed chunk in this isolated database. The isolated API is configured to query v2; do not run RAG until v2 coverage is complete.'
    }
    Write-Host "Version 2 completed-chunk coverage: $v2ChunkCount / $completedChunkCount"

    $requestJson = @{ query = $Question; top_k = 5 } | ConvertTo-Json -Compress
    try {
        $retrieval = Invoke-RestMethod -Method Post -Uri "$apiBase/api/retrieval/search" `
            -ContentType 'application/json' -Body $requestJson -TimeoutSec 60
    }
    catch {
        throw 'Retrieval request failed. Check API readiness, pgvector migration, and embedding maintenance; provider details were suppressed.'
    }
    $fixtureSources = @($retrieval.sources | Where-Object { $_.document_id -eq $upload.document_id })
    if ($fixtureSources.Count -lt 1) {
        throw 'Retrieval did not return a chunk from the uploaded synthetic fixture. Try the default question or check embedding maintenance.'
    }
    $qualifiedFixtureSources = @($fixtureSources | Where-Object { $_.score -ge 0.2 })
    if ($qualifiedFixtureSources.Count -lt 1) {
        throw 'The fixture was retrieved, but no source met the configured 0.2 minimum evidence score; RAG will safely refuse this question.'
    }
    $bestFixtureScore = ($qualifiedFixtureSources | Measure-Object -Property score -Maximum).Maximum
    Write-Host "Version 2 retrieval verified: model=$($retrieval.model), score=$bestFixtureScore, fixture=$($qualifiedFixtureSources[0].document_name)"

    $ragJson = @{ question = $Question } | ConvertTo-Json -Compress
    try {
        $answer = Invoke-RestMethod -Method Post -Uri "$apiBase/api/rag/answer" `
            -ContentType 'application/json' -Body $ragJson -TimeoutSec 300
    }
    catch {
        $httpStatus = 'unknown'
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $httpStatus = [string][int]$_.Exception.Response.StatusCode
        }
        $publicCode = 'unknown'
        $errorBody = [string]$_.ErrorDetails.Message
        if ($errorBody -match '"code"\s*:\s*"(rag_[a-z_]+)"') {
            $publicCode = $Matches[1]
        }
        throw "RAG API returned HTTP $httpStatus ($publicCode). Response content and provider details were suppressed."
    }
    $fixtureCitations = @($answer.citations | Where-Object {
        $_.document_id -eq $upload.document_id -and
        $_.chunk_id -in @($qualifiedFixtureSources | ForEach-Object { $_.chunk_id })
    })
    $citationIds = @($fixtureCitations | ForEach-Object { $_.citation_id })
    $supportedClaims = @($answer.claims | Where-Object {
        $_.status -eq 'supported' -and
        @($_.citation_ids | Where-Object { $_ -in $citationIds }).Count -gt 0
    })
    if ($supportedClaims.Count -lt 1) {
        throw 'RAG returned no supported claim citing the retrieved synthetic fixture. Review local model structured-output compatibility before another run; no model retry was made.'
    }

    Write-Host 'Stage 21 local backend demo passed.'
    Write-Host "API: $apiBase"
    Write-Host "Synthetic document: $($upload.filename) ($($upload.document_id))"
    Write-Host "Retrieved chunks from fixture: $($fixtureSources.Count)"
    Write-Host "Best qualified fixture score: $(($qualifiedFixtureSources | Measure-Object -Property score -Maximum).Maximum)"
    Write-Host "Supported claims citing fixture: $($supportedClaims.Count)"
    Write-Host "Citation: $($fixtureCitations[0].document_name), chunk $($fixtureCitations[0].chunk_id)"
    Write-Host "Model: $Model"
    Write-Host "Database volume is retained in Compose project '$ProjectName'; this script never removes it."
}
catch {
    Stop-Safely $_.Exception.Message
}
finally {
    if ($null -eq $oldApiPort) { Remove-Item Env:EKAA_API_PORT -ErrorAction SilentlyContinue }
    else { $env:EKAA_API_PORT = $oldApiPort }
    if ($null -eq $oldModel) { Remove-Item Env:EKAA_LLM_MODEL -ErrorAction SilentlyContinue }
    else { $env:EKAA_LLM_MODEL = $oldModel }
}
