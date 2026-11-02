# What If Local Studio

Content Machine local e gratuita para vídeos verticais (Instagram Reels). Você cola um **roteiro pronto** (de qualquer gênero: zumbi, POV, animais, crime, documentário, ficção, "E se...?") ou digita uma **ideia**, clica em **Gerar vídeo completo** e o programa faz o resto:

roteiro → interpretação → cenas → imagens → animação → narração em pt-BR → efeitos sonoros → legendas sincronizadas → edição → MP4.

Tudo roda no seu PC, sem API paga e sem créditos: **Ollama** (interpretação), **ComfyUI + FLUX.1 schnell** (imagens), **Wan 2.2 TI2V 5B** (animação), **Kokoro** (voz pt-BR), **faster-whisper** (tempo das palavras), **FFmpeg** (edição).

## Leia primeiro: o que está provado e o que ainda não está

Esta versão foi construída e testada num servidor Linux **sem GPU, sem Ollama e sem acesso ao seu Windows**. Por isso a lista honesta é esta:

| Parte | Estado | Como foi verificado |
|---|---|---|
| Workflows FLUX e Wan 2.2 | **Validados** contra um ComfyUI 0.43.0 real | Gerados dos templates oficiais (`flux_schnell`, `video_wan2_2_5B_ti2v`) com as definições reais dos nós; o validador do ComfyUI os aceita e recusa um workflow quebrado (controle negativo). Veja `workflows/PROVENANCE.md`. **Não foram executados com os pesos na GPU.** |
| Narração em português do Brasil | **Provada** | Voz Kokoro -> reconhecimento de fala (Whisper multilíngue offline) -> comparação com o texto: erro de palavras de 0 a 1,9% nas 3 vozes pt-BR (pf_dora 0%, pm_alex 1,9%, pm_santa 1,9%); o controle (mesma voz com fonemas de inglês) dá 59%. Fonemas conferidos (`dʒ` em "gravidade", `ɾ` em "por"). |
| Preservação do roteiro | **Testada** | O texto de cada cena é um trecho exato do que você colou (divisão feita em código, não pelo LLM). Testes confirmam palavra por palavra, inclusive nas legendas do MP4. |
| Edição: câmera, transições, legendas, SFX, mix, MP4 | **Testada** | FFmpeg real, ffprobe, checagem por pixel da legenda e do tempo. |
| Botão único, modo supervisionado, regenerar cena, animação velha descartada | **Testados** | Com Ollama e ComfyUI **simulados** (mas o ComfyUI simulado usa as definições reais dos nós). |
| **Ollama com modelo real** | **NÃO testado** | Sem acesso ao registro de modelos. A qualidade do JSON e dos prompts depende do modelo que você usar. |
| **FLUX gerando imagem real** | **NÃO testado** | Sem GPU. |
| **Wan 2.2 animando de verdade** | **NÃO testado** | Sem GPU. Tempo e VRAM são desconhecidos. |
| `setup.ps1`, `run.ps1`, `real_test.ps1` | **NÃO executados aqui** | Sem Windows. Revisados à mão. Na sua máquina o `setup`, o Ollama e o FLUX já funcionaram (relato do teste real). |
| Salvamento de arquivos no Windows (v0.2.1) | **Testado só por simulação** | Os bloqueios do Windows são simulados (os.replace falhando com PermissionError); threads e processos são testados de verdade. O comportamento com o seu antivírus/OneDrive só aparece rodando `diagnose_storage.py` aí. |
| Chatterbox, Piper | **NÃO testados** | Opcionais. |

Não considere o programa "funcionando" antes de rodar `.\real_test.ps1` (abaixo) e ver as etapas passarem.

## Requisitos

- Windows 10/11 com NVIDIA RTX (testado em teoria para 5060 Ti 16 GB). Driver **580 ou mais novo** para o PyTorch cu130 que o README oficial do ComfyUI recomenda hoje (com driver 570 a 579, use cu128: `-TorchIndex https://download.pytorch.org/whl/cu128`).
- Python 3.10 a 3.13 (3.12 recomendado), FFmpeg completo (com libass), Ollama.
- Espaço: ~9 GB (modelo do Ollama) + ~17 GB (FLUX fp8) + ~16 GB (Wan, opcional) + ~0,4 GB (voz).
- Sem Docker e sem WSL.

## Instalação

PowerShell na pasta do projeto. Leia o `setup.ps1` antes: ele não sobrescreve nada que já existe nem instala nada globalmente.

```powershell
.\setup.ps1                                        # ambiente do app + voz Kokoro + .env
.\setup.ps1 -InstallComfyUI -DownloadImageModel -PullOllamaModel
.\setup.ps1 -ComfyUIPath D:\ComfyUI -DownloadImageModel     # usa o seu ComfyUI, sem alterá-lo
.\setup.ps1 -ComfyUIPath D:\ComfyUI -DownloadWanModels      # modelos do Wan (modo completo)
```

Dica: o jeito mais simples de ter um ComfyUI funcionando em NVIDIA é o **Windows Portable** oficial (github.com/comfyanonymous/ComfyUI/releases). Extraia, rode uma vez e passe a pasta com `-ComfyUIPath`.

**Arquivos que começam com ponto** (`.env.example`, `.gitignore`) costumam sumir ao extrair ZIP ou subir para o GitHub. O projeto traz cópias sem ponto (`env.example`, `gitignore.template`) e o `setup.ps1` recria `.env`, `.env.example` e `.gitignore` se faltarem, sem sobrescrever os que existirem.

Depois: `.\run.ps1` abre Ollama, ComfyUI e a interface em http://localhost:8501.

## Pastas de dados e o erro "Acesso negado" no Windows

Se aparecer `PermissionError: [WinError 5] Acesso negado: project.json.tmp -> project.json` (ou WinError 32), outro programa estava com o arquivo aberto naquele instante: antivírus (Windows Defender examina cada arquivo recém-gravado), **OneDrive** (a Área de Trabalho costuma ser sincronizada) ou o indexador do Windows. O app grava o `project.json` dezenas de vezes por minuto, então mais cedo ou mais tarde bate num bloqueio.

O que o app faz agora (v0.2.1): grava em um temporário de nome **único** ao lado do arquivo, força para o disco e troca por `os.replace` com **retentativas e espera crescente** (até ~12 s). Se ainda assim não der, o `project.json` anterior fica intacto, a versão nova é guardada em `project.json.unsaved` e a mensagem diz o que fazer. Há uma cópia `project.json.bak`, usada se o principal ficar ilegível. Threads e processos diferentes não se atropelam (trava de thread + trava de arquivo), e a interface altera o projeto sempre por leitura-modificação-gravação sob trava, em vez de salvar uma cópia velha. Log, JSONs, imagens baixadas, WAV e MP4 também usam escrita segura; se o MP4 final estiver aberto no seu player, o vídeo novo sai com sufixo de hora em vez de falhar.

**Mudar as pastas sem mover nada** (o `.venv`, o ComfyUI e os modelos continuam onde estão). No `.env`:

```
WIF_PROJECTS_DIR=C:\WIF\projects
WIF_OUTPUTS_DIR=C:\WIF\outputs
WIF_TEST_OUTPUT_DIR=C:\WIF\testes
```

Aceita `~` e `%USERPROFILE%`. Vazio = pastas padrão dentro do projeto. Só para uma execução do teste: `.\real_test.ps1 -OutputDir C:\WIF\testes -ProjectsDir C:\WIF\projects`. Projetos que já estão na pasta antiga continuam lá: copie as pastas de `projects\` para a nova se quiser abri-los.

**Teste a sua pasta** (poucos segundos; martela com gravações e leituras simultâneas e conta os bloqueios):

```powershell
.\.venv\Scripts\python scripts\diagnose_storage.py                 # pastas configuradas
.\.venv\Scripts\python scripts\diagnose_storage.py C:\WIF D:\outra  # pastas específicas
```

Se mostrar muitas retentativas, mude a pasta para fora da Área de Trabalho/OneDrive ou adicione-a às exclusões do Windows Defender.

## Testes reais no seu PC (faça nesta ordem)

```powershell
.\real_test.ps1 -Steps armazenamento,ambiente,ollama,workflows,imagem,tts_pt    # 1) o essencial, poucos minutos
.\real_test.ps1 -Steps mp4_30s                                      # 2) MP4 de 30 s completo, sem animação
.\real_test.ps1 -Steps wan                                          # 3) uma animação do Wan: mede tempo e VRAM
.\real_test.ps1 -Steps tudo_um_botao                                # 4) roteiro curto -> MP4 com tudo (animação inclusa)
```

Cada etapa diz PASSOU, FALHOU ou PULADO, com tempos, pico de VRAM e o que fazer. O resultado fica em `real_test_output\relatorio.md` (e um ZIP pequeno para me enviar). Os MP4, imagens e áudio gerados ficam na mesma pasta: **assista e ouça**, porque nenhum teste automático julga se a imagem ficou bonita.

O que cada etapa realmente verifica: `armazenamento` (gravações simultâneas nas pastas de projetos, saídas e testes, contando bloqueios), `ambiente` (FFmpeg com os filtros certos, GPU e se o ComfyUI está usando CUDA), `ollama` (modelo instalado, JSON válido e **texto do roteiro idêntico**), `workflows` (nós e modelos instalados), `imagem` (FLUX gera 768x1344 que não é imagem vazia, com VRAM), `tts_pt` (narração + conferência por Whisper), `mp4_30s` (pipeline inteira em modo rápido + ffprobe), `wan` (animação decodifica e tem movimento), `tudo_um_botao` (roteiro -> MP4 com animação).

Se algo falhar, envie o `enviar_este_relatorio.zip`.

## Uso

1. Abra a interface (`.\run.ps1`). Barra lateral -> *Verificação do ambiente* -> *Verificar agora*.
2. Escolha **Colar meu roteiro** (preservado palavra por palavra) ou **Criar roteiro a partir de uma ideia**.
3. Escolha estilo visual (cinematográfico, 3D, cartoon, documental, anime, terror sombrio, POV, ilustração ou o seu próprio), gênero (ou automático), tom, voz, **modo** e **animação**.
4. **Gerar vídeo completo.** Veja a etapa, o passo, o progresso real e os logs.

**Marcadores no roteiro** (não são narrados): `---` ou `Parte 2` / `Episódio 3: Título` começam um novo episódio; `[direção entre colchetes numa linha só]` vira dica visual da próxima cena. Exemplos de vários gêneros em `examples\`.

**Modo automático**: faz todas as etapas sem parar. **Modo supervisionado**: pausa depois do plano, das imagens e das animações; você revisa, regenera o que quiser, aprova e clica de novo em *Gerar vídeo completo* (ele continua de onde parou e não refaz o que já está pronto).

**Regenerar uma cena**: troca a semente e refaz só ela. Regenerar a imagem **descarta a animação antiga** daquela cena; se a animação é de uma imagem diferente da atual, o editor ignora e avisa no log.

**Editar texto**: a narração fica travada. Para mudar uma palavra, marque *Permitir editar o texto da narração*; a cena fica marcada como editada.

## Como funciona

- **Divisão em cenas (código, sem LLM).** Frases são agrupadas em cenas de 3 a 7,5 s de fala, respeitando parágrafos, abreviações e frases longas. Depois a divisão é conferida palavra por palavra contra o seu roteiro; se divergir, o programa para.
- **Interpretação (Ollama).** Uma chamada deduz gênero, tom, personagens (com aparência fixa), cenários, paleta, luz e estilo. Outras chamadas, em lotes de 6 cenas, devolvem **só o visual** de cada trecho (prompt de imagem, movimento, câmera, efeito sonoro). O modelo não pode inventar personagem nem cenário fora da bíblia e **nunca devolve nem altera a narração**; se errar, o programa pede de novo com o motivo.
- **Consistência.** Todas as cenas reutilizam a mesma bíblia visual: aparência exata de cada personagem presente, descrição do cenário, paleta, luz e câmera. **Não há garantia de consistência perfeita** (o FLUX schnell não tem referência de personagem estável). Opcional: `consistency.share_seed_by_location` e o experimental `consistency.chain_img2img`, que gera a cena a partir da imagem da cena anterior quando cenário e personagem são os mesmos.
- **Imagens.** FLUX.1 schnell fp8 via ComfyUI, 768x1344. Libera a VRAM entre etapas (`/free`).
- **Narração.** Kokoro com fonemas pt-BR (espeak-ng embutido). Só vozes pt-BR são aceitas (`pf_dora`, `pm_alex`, `pm_santa`); voz inglesa daria sotaque estrangeiro. Números, moeda, ordinais, %, unidades e horas viram palavras **só na fala**; a legenda mostra o seu texto original.
- **Sincronia.** A duração de cada cena é a da voz + respiro. As palavras vêm do Whisper (multilíngue, CPU), alinhadas ao seu texto. Sem Whisper, divide proporcionalmente dentro da fala da cena e avisa.
- **Animação (modo completo).** Cada imagem vira vídeo com o Wan 2.2 TI2V 5B, com tamanho que acompanha a narração (4n+1 quadros, entre `min_length` e `max_length`). Se a cena for mais longa que a animação, a edição desacelera até 1,5x e termina com zoom lento na última imagem, em vez de congelar.
- **Efeitos e trilha.** Efeitos sintetizados (whoosh, impact, boom, rumble, riser, heartbeat, wind, drone), escolhidos pelo modelo conforme a ação do texto; o colchão sonoro discreto muda com o tom. Para sons reais, coloque arquivos CC0 em `assets\sfx\`. Trilha própria: `render.music_file`.
- **Edição.** Zoom/pan com easing, transição `fade` de 0,18 s, legenda ASS com destaque palavra a palavra, loudnorm -14 LUFS, H.264 + AAC, 1080x1920, 30 fps. O ffprobe confere o arquivo no fim e o resultado aparece na aba final.

## Otimização para RTX 5060 Ti 16 GB / 64 GB RAM / i7-14700KF

- Uma etapa de GPU por vez: Ollama descarrega o modelo ao terminar o roteiro; o ComfyUI libera a VRAM antes do Wan e depois dele.
- Whisper e Kokoro rodam na **CPU** (poupa VRAM e dispensa CUDA extra). O export H.264 usa a CPU; `render.encoder: h264_nvenc` usa a GPU se preferir.
- Modelos escolhidos para caber em 16 GB: FLUX schnell **fp8**, Wan 2.2 **5B fp16**, Qwen2.5 14B quantizado no Ollama.
- `animation.max_length` começa em 73 quadros (~3 s). Suba depois de medir o pico de VRAM no relatório.
- 64 GB de RAM deixam o ComfyUI descarregar modelos para a memória em vez de recarregar do disco.

## Licenças (confira antes de publicar)

Não consegui abrir as páginas dos modelos daqui. Isto é o que eu sei; **confirme nos cards oficiais**.

| Componente | Licença conhecida | Observação |
|---|---|---|
| FLUX.1 [schnell] | Apache-2.0 | Uso comercial permitido. O FLUX.1 [dev] NÃO é igual; não o use no lugar sem ler a licença. |
| Wan 2.2 TI2V 5B | Apache-2.0 | Confirme no repositório do modelo. |
| Kokoro-82M | Apache-2.0 | Voz padrão. |
| Qwen2.5-14B | Apache-2.0 | Alguns tamanhos da família têm outra licença. Gemma tem termos próprios. |
| Whisper / faster-whisper | MIT | |
| Chatterbox | MIT (código) | Áudio com marca d'água inaudível; só use voz de referência que você tem direito de usar. |
| Piper | Varia | O Piper atual é GPL-3.0 e cada voz tem licença própria. |
| ComfyUI | GPL-3.0 | Roda como processo separado. |

Vídeos com imagens realistas geradas por IA costumam exigir a marcação de conteúdo de IA ao publicar. Este programa **não publica nada** sozinho.

## Workflows

Gerados por `scripts\build_workflows.py` a partir dos templates oficiais e das definições reais do **seu** ComfyUI (rode de novo se atualizar o ComfyUI e algo quebrar: `python scripts\build_workflows.py --comfy http://127.0.0.1:8188`). Mapeamento por título de nó em `*.mapping.json`. Detalhes em `workflows\README.md`.

## Chatterbox e Piper (opcionais, sem teste)

Kokoro é o padrão. O **Chatterbox Multilingual** exige PyTorch com CUDA e deve ficar num ambiente separado (`py -3.11 -m venv chatterbox-env`, instale `torch` com o mesmo índice do ComfyUI e `chatterbox-tts`). Depois `tts.backend: chatterbox` e `tts.chatterbox.python: <caminho>\chatterbox-env\Scripts\python.exe`. Para **Piper**: `tts.backend: piper` e `tts.piper.model` apontando para um `.onnx` pt-BR.

## Estrutura

```
app.py                  interface Streamlit
config.yaml, .env       configuração
setup.ps1, run.ps1      instalação e execução
real_test.ps1           testes REAIS no seu PC (relatório)
src/wifstudio/          script, planner, comfy, ui2api, tts, align, subs, sfx, render, pipeline, jobs
workflows/              workflows API JSON (dos templates oficiais) + mapeamento por título
scripts/                build_workflows.py, real_check.py, chatterbox_worker.py
examples/               roteiros de exemplo de vários gêneros
assets/sfx/             seus efeitos sonoros (opcional)
models/                 voz Kokoro
projects/, outputs/     projetos (retomáveis) e MP4/SRT finais
tests/                  testes automatizados
```

## Testes automatizados (no desenvolvimento)

```powershell
.\.venv\Scripts\python -m pytest tests -q
```

Precisam dos modelos do Kokoro em `models\kokoro\`. Os testes de reconhecimento de fala usam um modelo ASR opcional (`WIF_TEST_ASR_DIR`) e os de validação contra ComfyUI real usam `WIF_REAL_COMFY_URL`; sem eles, esses testes são pulados.

## Problemas comuns

- **"ComfyUI inacessível"**: `.\run.ps1`, ou abra o ComfyUI e confira `COMFYUI_HOST` no `.env`.
- **"O workflow ... não é compatível"**: a mensagem lista o nó ou o arquivo de modelo que falta. Instale o modelo ou rode `scripts\build_workflows.py`.
- **CUDA out of memory**: reduza `image.width/height` (576x1024) ou `animation.max_length`; feche outros programas.
- **Voz estrangeira/sotaque**: o app recusa vozes que não sejam pt-BR; escolha `pf_dora`, `pm_alex` ou `pm_santa`.
- **Legenda com fonte errada**: use uma fonte instalada em `subtitles.font`.
- **Whisper não baixa**: o app segue com alinhamento proporcional e avisa. Rode uma vez com internet para baixar o modelo.
