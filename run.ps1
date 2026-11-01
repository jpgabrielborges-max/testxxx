<#
.SYNOPSIS  Inicia Ollama (se preciso), ComfyUI (se COMFYUI_PATH estiver no .env) e a interface Streamlit.
.PARAMETER NoComfyUI  Nao tenta iniciar o ComfyUI (use se voce ja o abriu por conta propria).
.PARAMETER Port       Porta da interface (padrao 8501).
#>
[CmdletBinding()]
param([switch]$NoComfyUI, [int]$Port = 8501)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Say($m)  { Write-Host "[run] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[aviso] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[ERRO] $m" -ForegroundColor Red; exit 1 }

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Fail "Ambiente nao encontrado. Rode .\setup.ps1 primeiro." }

$envMap = @{}
if (Test-Path ".env") {
  foreach ($l in Get-Content ".env") { if ($l -match "^\s*([A-Z_]+)=(.*)$") { $envMap[$Matches[1]] = $Matches[2].Trim() } }
}
$ollamaHost = if ($envMap["OLLAMA_HOST"]) { $envMap["OLLAMA_HOST"] } else { "http://127.0.0.1:11434" }
$comfyHost = if ($envMap["COMFYUI_HOST"]) { $envMap["COMFYUI_HOST"] } else { "http://127.0.0.1:8188" }

function Wait-Http($url, $seconds) {
  $end = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $end) {
    try { Invoke-RestMethod -Uri $url -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Seconds 2 }
  }
  return $false
}

# Ollama
if (Wait-Http "$ollamaHost/api/tags" 2) { Say "Ollama ja esta rodando" }
elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
  Say "Iniciando o Ollama"
  Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
  if (-not (Wait-Http "$ollamaHost/api/tags" 30)) { Warn "Ollama nao respondeu em 30 s. Abra o aplicativo do Ollama manualmente." }
} else { Warn "Ollama nao encontrado. Instale-o para gerar roteiros." }

# ComfyUI
if (-not $NoComfyUI) {
  if (Wait-Http "$comfyHost/system_stats" 2) { Say "ComfyUI ja esta rodando" }
  elseif ($envMap["COMFYUI_PATH"] -and (Test-Path (Join-Path $envMap["COMFYUI_PATH"] "main.py"))) {
    $cdir = $envMap["COMFYUI_PATH"]
    $cpy = Join-Path $cdir ".venv\Scripts\python.exe"
    if (-not (Test-Path $cpy)) { $cpy = $venvPy; Warn "ComfyUI sem .venv proprio; usando o Python do aplicativo (nao recomendado)." }
    $cargs = if ($envMap["COMFYUI_ARGS"]) { $envMap["COMFYUI_ARGS"] } else { "--listen 127.0.0.1 --port 8188" }
    Say "Iniciando o ComfyUI em uma nova janela (pode levar um minuto)"
    Start-Process -FilePath $cpy -ArgumentList "main.py $cargs" -WorkingDirectory $cdir
    if (-not (Wait-Http "$comfyHost/system_stats" 180)) { Warn "ComfyUI nao respondeu em 180 s. Veja a janela dele para erros." }
  } else { Warn "COMFYUI_PATH nao definido no .env: inicie o ComfyUI manualmente." }
}

Say "Abrindo a interface em http://localhost:$Port"
& $venvPy -m streamlit run app.py --server.port $Port --browser.gatherUsageStats false
