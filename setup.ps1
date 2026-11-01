<#
.SYNOPSIS
  Prepara o What If Local Studio no Windows (instalacao nativa, sem Docker e sem WSL).

.DESCRIPTION
  Por padrao so faz o que e seguro e pequeno: diagnostica o sistema, cria o ambiente Python do APLICATIVO (.venv),
  baixa a voz Kokoro (~350 MB) e cria o .env. Nada e sobrescrito e nada e instalado globalmente.
  O que e pesado ou envolve outra instalacao so acontece se voce pedir com um parametro.

.PARAMETER InstallComfyUI      Clona o ComfyUI em .\ComfyUI com ambiente Python PROPRIO (PyTorch CUDA para RTX 50).
.PARAMETER ComfyUIPath         Usa uma instalacao do ComfyUI que voce ja tem (nao toca nela, so grava o caminho no .env).
.PARAMETER TorchIndex          Indice de wheels do PyTorch. Padrao cu128 (primeiro CUDA com suporte a Blackwell/RTX 50).
.PARAMETER DownloadImageModel  Baixa o FLUX.1 schnell fp8 (~17 GB) para a pasta de modelos do ComfyUI, se ainda nao existir.
.PARAMETER DownloadWanModels   Baixa os arquivos do Wan 2.2 TI2V 5B (~16 GB no total), se ainda nao existirem.
.PARAMETER PullOllamaModel     Executa "ollama pull" do modelo configurado (~9 GB).
.PARAMETER SkipKokoro          Nao baixa a voz Kokoro.

.EXAMPLE
  .\setup.ps1
  .\setup.ps1 -InstallComfyUI -DownloadImageModel -PullOllamaModel
  .\setup.ps1 -ComfyUIPath D:\ComfyUI
#>
[CmdletBinding()]
param(
  [switch]$InstallComfyUI,
  [string]$ComfyUIPath = "",
  [string]$TorchIndex = "https://download.pytorch.org/whl/cu128",
  [switch]$DownloadImageModel,
  [switch]$DownloadWanModels,
  [switch]$PullOllamaModel,
  [switch]$SkipKokoro
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

function Say($m)  { Write-Host "[setup] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ ok  ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[aviso] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[ERRO ] $m" -ForegroundColor Red; exit 1 }

function Find-Python {
  foreach ($cand in @("py -3.12", "py -3.11", "py -3.10", "python")) {
    $parts = $cand -split " "
    $exe = $parts[0]
    $extra = @()
    if ($parts.Length -gt 1) { $extra = $parts[1..($parts.Length - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
      $v = (& $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    } catch { continue }
    if ($v -match "^3\.(10|11|12)$") { return @{ Exe = $exe; Args = $extra; Version = $v } }
  }
  return $null
}

function Download-File($url, $dest) {
  if (Test-Path $dest) { Ok "ja existe, nao vou sobrescrever: $dest"; return }
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  $tmp = "$dest.part"
  Say "baixando $url"
  & curl.exe -L --fail --retry 3 -C - -o $tmp $url
  if ($LASTEXITCODE -ne 0) { Fail "Falha no download de $url (codigo $LASTEXITCODE). Baixe manualmente e salve em: $dest" }
  Move-Item $tmp $dest
  Ok "salvo: $dest"
}

function Set-EnvVar($file, $key, $value) {
  $lines = if (Test-Path $file) { Get-Content $file } else { @() }
  $found = $false
  $out = foreach ($l in $lines) { if ($l -match "^\s*$key=") { $found = $true; "$key=$value" } else { $l } }
  if (-not $found) { $out = @($out) + "$key=$value" }
  Set-Content -Path $file -Value $out -Encoding UTF8
}

# ------------------------------------------------------------------ 1. diagnostico
Say "Diagnostico do sistema"
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
  Fail "nvidia-smi nao encontrado. Instale o driver NVIDIA (570 ou mais novo para a RTX 5060 Ti) e rode de novo."
}
$gpuLine = (& nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader | Select-Object -First 1)
$g = $gpuLine -split ",\s*"
Ok "GPU: $($g[0]) | driver $($g[1]) | VRAM $($g[2]) | compute capability $($g[3])"
try { if ([version]$g[1] -lt [version]"570.0") { Warn "Driver $($g[1]) e antigo para GPUs RTX 50 (Blackwell). Atualize para 570 ou mais novo." } } catch { }
if ($g[3] -match "^12") { Ok "Arquitetura Blackwell (sm_120): o PyTorch precisa ser build CUDA 12.8 ou mais novo (o ComfyUI deste setup usa $TorchIndex)." }

$py = Find-Python
if (-not $py) { Fail "Python 3.10 a 3.12 nao encontrado. Instale: winget install -e --id Python.Python.3.12" }
Ok "Python $($py.Version)"

foreach ($t in @("ffmpeg", "ffprobe")) {
  if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { Fail "$t nao encontrado no PATH. Instale: winget install -e --id Gyan.FFmpeg  (e abra um novo terminal)" }
}
Ok "FFmpeg/FFprobe encontrados"
$filters = (& ffmpeg -hide_banner -filters 2>$null) -join "`n"
if ($filters -notmatch "\bass\b" -or $filters -notmatch "xfade" -or $filters -notmatch "zoompan") {
  Fail "Este FFmpeg nao tem os filtros ass/xfade/zoompan (precisa de libass). Use o build 'full' (winget install Gyan.FFmpeg)."
}
Ok "FFmpeg com libass, xfade e zoompan"

$ollamaModel = "qwen2.5:14b"
if (Test-Path ".env") { $m = Select-String -Path ".env" -Pattern "^OLLAMA_MODEL=(.+)$" | Select-Object -First 1; if ($m) { $ollamaModel = $m.Matches[0].Groups[1].Value.Trim() } }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Warn "Ollama nao encontrado. Instale: winget install -e --id Ollama.Ollama  (depois: ollama pull $ollamaModel)"
} else {
  Ok "Ollama encontrado"
  if ($PullOllamaModel) { Say "ollama pull $ollamaModel"; & ollama pull $ollamaModel; if ($LASTEXITCODE -ne 0) { Fail "ollama pull falhou" } }
  else {
    $have = (& ollama list 2>$null) -join "`n"
    if ($have -notmatch [regex]::Escape(($ollamaModel -split ":")[0])) { Warn "Modelo $ollamaModel nao instalado. Rode: ollama pull $ollamaModel   (ou .\setup.ps1 -PullOllamaModel)" }
    else { Ok "Modelo $ollamaModel disponivel" }
  }
}

# ------------------------------------------------------------------ 2. ambiente do aplicativo
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Say "Criando ambiente Python do aplicativo em .venv"
  & $py.Exe @($py.Args) -m venv .venv
  if ($LASTEXITCODE -ne 0) { Fail "Falha ao criar o venv" }
} else { Ok ".venv ja existe (mantido)" }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Say "Instalando dependencias do aplicativo (requirements.txt)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install falhou. Veja a mensagem acima." }
& $venvPy -c "import streamlit, pydantic, kokoro_onnx, faster_whisper, num2words; print('imports ok')"
if ($LASTEXITCODE -ne 0) { Fail "Falha ao importar as dependencias." }

# ------------------------------------------------------------------ 3. voz Kokoro
if (-not $SkipKokoro) {
  $base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
  Download-File "$base/kokoro-v1.0.onnx" (Join-Path $PSScriptRoot "models\kokoro\kokoro-v1.0.onnx")
  Download-File "$base/voices-v1.0.bin" (Join-Path $PSScriptRoot "models\kokoro\voices-v1.0.bin")
}

# ------------------------------------------------------------------ 4. .env
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Ok ".env criado a partir do .env.example" } else { Ok ".env ja existe (mantido)" }

# ------------------------------------------------------------------ 5. ComfyUI
$comfy = ""
if ($ComfyUIPath) {
  if (-not (Test-Path (Join-Path $ComfyUIPath "main.py"))) { Fail "$ComfyUIPath nao parece uma pasta do ComfyUI (sem main.py)." }
  $comfy = (Resolve-Path $ComfyUIPath).Path
  Ok "Usando ComfyUI existente em $comfy (nada foi alterado la)"
} elseif ($InstallComfyUI) {
  $comfy = Join-Path $PSScriptRoot "ComfyUI"
  if (Test-Path (Join-Path $comfy "main.py")) {
    Warn "ComfyUI ja existe em $comfy. Nao vou sobrescrever nem reinstalar."
  } else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git nao encontrado. Instale: winget install -e --id Git.Git" }
    Say "Clonando ComfyUI"
    & git clone https://github.com/comfyanonymous/ComfyUI.git $comfy
    if ($LASTEXITCODE -ne 0) { Fail "git clone falhou" }
    Say "Criando ambiente Python proprio do ComfyUI"
    & $py.Exe @($py.Args) -m venv (Join-Path $comfy ".venv")
    $cpy = Join-Path $comfy ".venv\Scripts\python.exe"
    & $cpy -m pip install --upgrade pip --quiet
    Say "Instalando PyTorch ($TorchIndex). Sao alguns GB."
    & $cpy -m pip install torch torchvision torchaudio --index-url $TorchIndex
    if ($LASTEXITCODE -ne 0) { Fail "Falha ao instalar o PyTorch. Se o indice $TorchIndex mudou, passe -TorchIndex com o indice atual (pytorch.org/get-started)." }
    & $cpy -m pip install -r (Join-Path $comfy "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "Falha ao instalar os requisitos do ComfyUI" }
  }
  $cpy = Join-Path $comfy ".venv\Scripts\python.exe"
  if (Test-Path $cpy) {
    Say "Verificando PyTorch + CUDA na sua GPU (nao assumo que funciona)"
    & $cpy -c "import torch; print('torch', torch.__version__, '| cuda', torch.version.cuda, '| disponivel', torch.cuda.is_available()); print('capability', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None); print('sm_120 na build:', 'sm_120' in torch.cuda.get_arch_list())"
    $okCuda = (& $cpy -c "import torch; print(torch.cuda.is_available() and 'sm_120' in torch.cuda.get_arch_list())")
    if ($okCuda -ne "True") { Warn "O PyTorch nao enxerga a GPU ou a build nao tem sm_120 (Blackwell). A geracao de imagens NAO vai funcionar ate corrigir isso. Tente outro -TorchIndex (ex.: cu130) ou o ComfyUI Portable oficial para RTX 50." }
    else { Ok "PyTorch com CUDA e sm_120 funcionando" }
  }
} else {
  Warn "ComfyUI nao configurado. Rode com -InstallComfyUI ou -ComfyUIPath <pasta>."
}
if ($comfy) { Set-EnvVar (Join-Path $PSScriptRoot ".env") "COMFYUI_PATH" $comfy }

# ------------------------------------------------------------------ 6. modelos do ComfyUI (opcionais, grandes)
if (($DownloadImageModel -or $DownloadWanModels) -and -not $comfy) { Fail "Para baixar modelos informe -InstallComfyUI ou -ComfyUIPath." }
if ($DownloadImageModel) {
  Warn "FLUX.1 schnell (Apache-2.0). Arquivo grande (~17 GB). Se a URL abaixo mudar, baixe manualmente em Comfy-Org/flux1-schnell no Hugging Face."
  Download-File "https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors" (Join-Path $comfy "models\checkpoints\flux1-schnell-fp8.safetensors")
}
if ($DownloadWanModels) {
  $hf = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files"
  Download-File "$hf/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" (Join-Path $comfy "models\diffusion_models\wan2.2_ti2v_5B_fp16.safetensors")
  Download-File "$hf/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" (Join-Path $comfy "models\text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors")
  Download-File "$hf/vae/wan2.2_vae.safetensors" (Join-Path $comfy "models\vae\wan2.2_vae.safetensors")
}

# ------------------------------------------------------------------ resumo
Write-Host ""
Ok "Setup concluido."
Write-Host "Proximos passos:"
Write-Host "  1. Garanta o modelo do Ollama:   ollama pull $ollamaModel"
Write-Host "  2. Garanta o modelo de imagem:   $(if ($comfy) { Join-Path $comfy 'models\checkpoints\flux1-schnell-fp8.safetensors' } else { 'ComfyUI\models\checkpoints\flux1-schnell-fp8.safetensors' })"
Write-Host "  3. Inicie tudo:                  .\run.ps1"
Write-Host "  4. Na aba lateral, clique em 'Verificar agora' para conferir Ollama, ComfyUI e GPU."
