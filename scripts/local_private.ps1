param(
    [Parameter(Position = 0)]
    [ValidateSet("check", "generate", "agent-init", "agent-validate", "agent-run", "agent-select", "agent-revise", "agent-refresh", "compare", "verify", "compose-config")]
    [string]$Command = "check",

    [string]$Excel,
    [string]$Workspace,
    [string]$OutputDir,
    [string]$Model,
    [int]$Page,
    [string]$RunName,
    [string]$BaseRunName,
    [string]$Prompt = "Create an executive management deck with traceable insights and deterministic metrics.",
    [string]$Sections = "Data overview,Key differences and trends,Risks and opportunities,Actions"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Python = if ($env:SLIDEGEN_PYTHON) {
    $env:SLIDEGEN_PYTHON
} elseif (Test-Path $VenvPython) {
    $VenvPython
} else {
    "python"
}

# This launcher is intentionally opinionated: it never chooses a cloud provider.
$env:LLM_PROVIDER = "ollama"
$env:LLM_PRIVACY_MODE = "local_only"
# Never forward an ambient cloud-capable generic key to host Ollama.
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
$BaseUrlWasProvided = -not [string]::IsNullOrWhiteSpace($env:LLM_BASE_URL)
if (-not $BaseUrlWasProvided) {
    if ($Command -eq "compose-config") {
        $env:LLM_BASE_URL = "http://host.docker.internal:11434/v1"
    } else {
        $env:LLM_BASE_URL = "http://localhost:11434/v1"
    }
}
if (-not $env:LLM_LOCAL_ENDPOINT_ALLOWLIST) {
    $env:LLM_LOCAL_ENDPOINT_ALLOWLIST = "localhost,127.0.0.1,host.docker.internal,ollama,vllm"
}
if ($Model) {
    $env:LLM_MODEL_DEFAULT = $Model
} elseif (-not $env:LLM_MODEL_DEFAULT) {
    $env:LLM_MODEL_DEFAULT = "qwen2.5:7b"
}
if (-not $env:LLM_TOOL_MODE) { $env:LLM_TOOL_MODE = "json" }
if (-not $env:LLM_JSON_MODE) { $env:LLM_JSON_MODE = "prompt" }
if (-not $env:LLM_SYSTEM_MODE) { $env:LLM_SYSTEM_MODE = "merge" }
if (-not $env:LLM_MAX_PARALLEL) { $env:LLM_MAX_PARALLEL = "4" }
$env:PYTHONPATH = Join-Path $RepoRoot "src"

function Require-Value {
    param(
        [string]$Value,
        [string]$Name
    )
    if (-not $Value) { throw "$Name is required for command '$Command'." }
}

Push-Location $RepoRoot
try {
    switch ($Command) {
        "check" {
            & $Python -m ppt_generation.run_pipeline --check-llm
        }
        "generate" {
            Require-Value $Excel "-Excel"
            $Arguments = @(
                "scripts\generate_deck.py",
                "--excel", $Excel,
                "--prompt", $Prompt,
                "--sections", $Sections
            )
            if ($OutputDir) { $Arguments += @("--output-dir", $OutputDir) }
            & $Python @Arguments
        }
        "agent-init" {
            Require-Value $Excel "-Excel"
            Require-Value $Workspace "-Workspace"
            & $Python -m tools.local_agent_workspace init `
                --workspace $Workspace `
                --excel $Excel `
                --prompt $Prompt `
                --sections $Sections
        }
        "agent-validate" {
            Require-Value $Workspace "-Workspace"
            & $Python -m tools.local_agent_workspace validate --workspace $Workspace
        }
        "agent-run" {
            Require-Value $Workspace "-Workspace"
            & $Python -m tools.local_agent_workspace run --workspace $Workspace
        }
        "agent-select" {
            Require-Value $Workspace "-Workspace"
            if ($Page -le 0) { throw "-Page must be a positive content-page number." }
            $Arguments = @(
                "-m", "tools.local_agent_workspace", "select",
                "--workspace", $Workspace,
                "--page", $Page
            )
            if ($RunName) { $Arguments += @("--run-name", $RunName) }
            & $Python @Arguments
        }
        "agent-revise" {
            Require-Value $Workspace "-Workspace"
            & $Python -m tools.local_agent_workspace revise --workspace $Workspace
        }
        "agent-refresh" {
            Require-Value $Workspace "-Workspace"
            $Arguments = @(
                "-m", "tools.local_agent_workspace", "refresh",
                "--workspace", $Workspace
            )
            if ($BaseRunName) { $Arguments += @("--base-run-name", $BaseRunName) }
            if ($RunName) { $Arguments += @("--run-name", $RunName) }
            & $Python @Arguments
        }
        "compare" {
            & $Python -m tools.compare_models `
                --provider ollama `
                --models $env:LLM_MODEL_DEFAULT `
                --repeat 1
        }
        "verify" {
            # Deterministic unit tests inject synthetic standard settings. Keep
            # the test subprocess independent from this launcher's runtime gate.
            Remove-Item Env:LLM_PRIVACY_MODE -ErrorAction SilentlyContinue
            & $Python scripts\verify_all.py
        }
        "compose-config" {
            docker compose -f docker-compose.yml -f docker-compose.local.yml config
        }
    }

    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
