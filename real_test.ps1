<#
.SYNOPSIS
  Testes REAIS no seu Windows: Ollama, ComfyUI (FLUX e Wan 2.2), voz pt-BR, FFmpeg e o botao unico de ponta a ponta.
  Nada de servidores falsos. Gera real_test_output\relatorio.md e um ZIP pequeno para voce me enviar.

.PARAMETER Steps  Etapas separadas por virgula. Padrao: todas, na ordem de prioridade.
                  armazenamento, ambiente, ollama, workflows, imagem, tts_pt, mp4_30s, wan, tudo_um_botao
.PARAMETER Skip   Etapas a pular.
.PARAMETER OutputDir    Pasta dos arquivos de teste (MP4, imagens, relatorio). Vale so para esta execucao. Fixo: WIF_TEST_OUTPUT_DIR no .env.
.PARAMETER ProjectsDir  Pasta dos projetos nesta execucao. Fixo: WIF_PROJECTS_DIR no .env.
                        Escolha pastas FORA da Area de Trabalho e do OneDrive (ex.: C:\WIF\testes). Nao precisa mover o .venv nem os modelos.

.EXAMPLE
  .\real_test.ps1                                   # tudo (pode levar de 20 min a algumas horas, por causa do Wan)
  .\real_test.ps1 -Steps ambiente,ollama,workflows,imagem,tts_pt     # primeiro, so o essencial
  .\real_test.ps1 -Skip wan,tudo_um_botao
#>
[CmdletBinding()]
param([string]$Steps = "", [string]$Skip = "", [string]$OutputDir = "", [string]$ProjectsDir = "")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Say($m)  { Write-Host "[teste] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[aviso] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[ERRO ] $m" -ForegroundColor Red; exit 1 }

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Fail "Rode .\setup.ps1 primeiro." }

$envMap = @{}
if (Test-Path ".env") { foreach ($l in Get-Content ".env") { if ($l -match "^\s*([A-Z_]+)=(.*)$") { $envMap[$Matches[1]] = $Matches[2].Trim() } } }
if ($OutputDir)   { $env:WIF_TEST_OUTPUT_DIR = $OutputDir }
if ($ProjectsDir) { $env:WIF_PROJECTS_DIR = $ProjectsDir }
$ollama = if ($envMap["OLLAMA_HOST"]) { $envMap["OLLAMA_HOST"] } else { "http://127.0.0.1:11434" }
$comfy  = if ($envMap["COMFYUI_HOST"]) { $envMap["COMFYUI_HOST"] } else { "http://127.0.0.1:8188" }
foreach ($pair in @(@("Ollama", "$ollama/api/tags"), @("ComfyUI", "$comfy/system_stats"))) {
  try { Invoke-RestMethod -Uri $pair[1] -TimeoutSec 5 | Out-Null; Say "$($pair[0]) respondendo" }
  catch { Warn "$($pair[0]) NAO respondeu em $($pair[1]). Rode .\run.ps1 (ou abra o programa) e tente de novo. As etapas que dependem dele vao falhar." }
}

$argsList = @("scripts\real_check.py")
if ($Steps) { $argsList += @("--steps", $Steps) }
if ($Skip)  { $argsList += @("--skip", $Skip) }
& $py @argsList
$code = $LASTEXITCODE

$outRaw = if ($env:WIF_TEST_OUTPUT_DIR) { $env:WIF_TEST_OUTPUT_DIR } elseif ($envMap["WIF_TEST_OUTPUT_DIR"]) { $envMap["WIF_TEST_OUTPUT_DIR"] } else { "real_test_output" }
$outRaw = [Environment]::ExpandEnvironmentVariables($outRaw)
if ($outRaw.StartsWith("~")) { $outRaw = Join-Path $HOME $outRaw.Substring(1).TrimStart("\", "/") }
$out = if ([System.IO.Path]::IsPathRooted($outRaw)) { $outRaw } else { Join-Path $PSScriptRoot $outRaw }
if (Test-Path (Join-Path $out "relatorio.md")) {
  $zip = Join-Path $out "enviar_este_relatorio.zip"
  if (Test-Path $zip) { Remove-Item $zip }
  $items = @("relatorio.md", "relatorio.json") + (Get-ChildItem $out -Recurse -Filter "log.txt" | ForEach-Object { $_.FullName.Substring($out.Length + 1) })
  Push-Location $out
  Compress-Archive -Path $items -DestinationPath $zip
  Pop-Location
  Say "Relatorio: $out\relatorio.md"
  Say "Envie este arquivo: $zip (os MP4/imagens ficam na mesma pasta; veja e me diga o que achou)"
}
exit $code
