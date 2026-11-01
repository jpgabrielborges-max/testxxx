# What If Local Studio

Gerador local de vídeos verticais "E se...?" para Instagram Reels. Você digita uma hipótese, o programa escreve o roteiro em português, gera as imagens, narra, legenda e entrega um MP4 1080x1920. Tudo roda no seu PC, sem API paga.

Fluxo: **Ollama** (roteiro em JSON) → **ComfyUI + FLUX.1 schnell** (imagens) → **Wan 2.2 TI2V 5B** (animação opcional) → **Kokoro** (narração pt-BR) → **faster-whisper** (tempo das palavras) → **FFmpeg** (câmera, transições, legendas, mix, export).

## O que já foi testado e o que ainda não foi

Esta primeira versão foi escrita e testada num servidor Linux **sem GPU**. Por isso:

| Parte | Estado |
|---|---|
| Roteiro (cliente Ollama, JSON validado por Pydantic, retentativa por orçamento de palavras, memória da série) | Testado contra um Ollama **falso**. Nunca rodou com um modelo real. |
| Cliente do ComfyUI (envio, acompanhamento, download, timeout, erros, cancelamento) e troca de prompt/seed por **título** de nó | Testado contra um ComfyUI **falso** que valida o workflow como o real. |
| Workflow FLUX schnell e workflow Wan 2.2 | **Escritos de memória, nunca executados.** O app compara cada workflow com o seu ComfyUI (`/object_info`) antes de rodar e para com uma mensagem clara se algo não bater. |
| Narração Kokoro pt-BR, normalização de números, alinhamento, legendas ASS/SRT, câmera, transições, mix, MP4 final | **Testado de verdade** (modelo real, FFmpeg real, ffprobe, checagem de pixels da legenda). |
| Interface Streamlit | Teste automatizado de fluxo (gerar roteiro e aprovar). Nunca aberta num navegador com GPU. |
| `setup.ps1` e `run.ps1` | **Não executados** (não havia Windows). Revisados à mão. Leia o que fazem antes de rodar. |
| Chatterbox, Piper | Código escrito, **não testado**. |
| Medição de VRAM e tempo | Implementada com `nvidia-smi`, nunca rodou com GPU. |
| Wan 2.2 no seu hardware | **Sem medição ainda.** Os valores padrão em `config.yaml` são conservadores, mas são palpite. |

O teste de aceitação (gravidade por 5 segundos, 30 s) foi feito com roteiro e imagens de **fixture**, então ele prova a montagem, não a qualidade criativa. A qualidade real do roteiro e das imagens só aparece no seu PC.

## Requisitos

- Windows 10/11, NVIDIA com driver **570 ou mais novo** (RTX 50 / Blackwell), Python 3.10 a 3.12, FFmpeg completo (com libass), Ollama.
- Espaço: ~9 GB (modelo do Ollama) + ~17 GB (FLUX fp8) + ~16 GB (Wan, opcional) + ~0,4 GB (voz).
- Sem Docker e sem WSL.

## Instalação

Abra o PowerShell na pasta do projeto. Leia o `setup.ps1` antes: ele não sobrescreve nada que já existe e não instala nada globalmente.

```powershell
# básico: ambiente do app, voz Kokoro, .env
.\setup.ps1

# com ComfyUI novo em .\ComfyUI (ambiente próprio, PyTorch CUDA 12.8) e o modelo de imagem
.\setup.ps1 -InstallComfyUI -DownloadImageModel -PullOllamaModel

# ou usando um ComfyUI que você já tem (não é alterado)
.\setup.ps1 -ComfyUIPath D:\ComfyUI -DownloadImageModel

# modelos do Wan 2.2 (opcional, para o modo completo)
.\setup.ps1 -ComfyUIPath D:\ComfyUI -DownloadWanModels
```

O setup **verifica** se o PyTorch enxerga a GPU e se a build tem `sm_120`. Se disser que não, a geração de imagens não vai funcionar e a mensagem diz o que tentar. As URLs de download dos modelos não puderam ser conferidas daqui. Se alguma der 404, baixe o arquivo manualmente para a pasta indicada.

Para iniciar tudo: `.\run.ps1` (abre Ollama, ComfyUI e a interface em http://localhost:8501).

## Primeiro uso, passo a passo

1. Barra lateral → **Verificação do ambiente** → *Verificar agora*. Tudo verde antes de seguir.
2. Escreva a hipótese, escolha duração, episódios, estilo, tom e voz. **Gerar roteiro**.
3. Aba *Roteiro*: edite narração e prompts à vontade. **Editar e aprovar roteiro** libera as etapas seguintes.
4. Aba *Imagens*: **Gerar imagens**. Cena ruim? **Regenerar** troca a semente e refaz só ela.
5. (Modo completo) Aba *Animações*: marque as cenas, **Verificar workflow Wan**, **Gerar animações**.
6. Aba *Vídeo final*: **Gerar vídeo final** (faz a narração que faltar). Baixe o MP4 e o SRT, ou abra a pasta.

O progresso é real: a barra conta cenas concluídas, e cada etapa mostra o passo atual e o log. Erros ficam visíveis por cena. Os projetos ficam em `projects/<id>/` e podem ser reabertos. Cópias dos vídeos vão para `outputs/<id>/`.

## Como funciona cada parte

**Roteiro.** Duas chamadas ao Ollama com JSON schema: a "bíblia" da série (universo, personagens, paleta, cenário, luz, câmera, plano dos episódios) e depois cada episódio. O programa calcula um orçamento de cenas e palavras para a duração pedida e **pede de novo ao modelo** se o texto vier fora de ±20%. Números viram palavras por extenso (`textnorm.py`) para a voz ler bem e a legenda bater com a fala. Cada cena é marcada como *fato* ou *especulação*. Os episódios seguintes recebem o resumo dos anteriores, e todos os prompts de imagem recebem a bíblia visual.

**Imagens.** Prompt da cena + aparência dos personagens presentes + universo + paleta + luz + câmera + sufixo do estilo. Não há garantia de consistência perfeita entre cenas: o que existe é prompt compartilhado e semente determinística. Referência por imagem (IP-Adapter etc.) **não** foi implementada, porque o FLUX schnell não tem uma opção estável para isso.

**Animação (modo completo).** Cena a cena, imagem → Wan 2.2 TI2V 5B via ComfyUI. Antes de carregar o Wan o app libera a VRAM do FLUX (`/free`). Mede tempo e pico de VRAM por cena. Cenas sem animação seguem com zoom/pan. Se o workflow do Wan não bater com seu ComfyUI, só essa etapa falha e o modo rápido continua funcionando.

**Narração e sincronia.** A duração de cada cena é a duração real da voz + respiro. Se o episódio passar de 5% do alvo, o app refaz a voz até 1,15x. As palavras vêm do Whisper (modelo multilíngue `small`), alinhadas ao texto do roteiro. Se o Whisper não estiver disponível, usa distribuição proporcional dentro da fala da cena (a duração dela é exata) e avisa no log.

**Montagem.** Zoom/pan com easing (FFmpeg `zoompan`), transição `fade` de 0,18 s (`xfade`) sem alterar a duração total, legenda ASS com destaque palavra a palavra queimada no vídeo, SFX procedurais suaves e trilha opcional com ducking, loudnorm para -14 LUFS, H.264 + AAC, 1080x1920, 30 fps. Depois de exportar, o app roda o **ffprobe** e checa codec, resolução, fps, duração, sincronia áudio/vídeo, volume e decodificação completa. O resultado aparece na aba final.

## Ajustes importantes (`config.yaml`)

- `ollama.model`: troque o modelo à vontade. Se ele tiver "thinking" (ex.: Qwen3), use `ollama.think: false`.
- `image.width/height`: 768x1344. Se faltar VRAM, baixe para 576x1024.
- `animation.length`: precisa ser 4n+1. Comece pequeno e meça.
- `render.encoder: h264_nvenc` acelera o export final usando a GPU.
- `render.music_file`: trilha livre de direitos (o app não baixa música).
- `subtitles.*`: fonte, tamanho, cores e posição.

## Workflows do ComfyUI

Os workflows ficam em `workflows/` no formato **API JSON**, com um arquivo `*.mapping.json` ao lado. O mapeamento aponta para os nós pelo **título** (`WIF Positive Prompt` etc.), nunca por ID, então você pode trocar o workflow sem mexer no código. Veja `workflows/README.md` para exportar o seu e mapear.

## Licenças (confira antes de publicar)

Não consegui verificar as páginas dos modelos daqui. Isto é o que eu sei, **confirme nos cards oficiais**:

| Componente | Licença conhecida | Observação |
|---|---|---|
| FLUX.1 [schnell] | Apache-2.0 | Uso comercial permitido. O FLUX.1 [dev] NÃO é igual: não use no lugar sem ler a licença. |
| Wan 2.2 TI2V 5B | Apache-2.0 | Confirme no repositório do modelo. |
| Kokoro-82M | Apache-2.0 | Voz padrão do projeto. |
| Qwen2.5-14B | Apache-2.0 | Alguns tamanhos da família têm outra licença. Gemma tem termos próprios com restrições de uso. |
| Whisper / faster-whisper | MIT | |
| Chatterbox | MIT (código) | O áudio sai com marca d'água inaudível. Só use voz de referência que você tem direito de usar. |
| Piper | Varia | O Piper atual é GPL-3.0 e cada voz tem licença própria. |
| ComfyUI | GPL-3.0 | Roda como processo separado. |

Para Instagram, vídeos com imagens realistas geradas por IA costumam exigir a marcação de conteúdo de IA na publicação. Este programa **não publica nada** sozinho.

## Chatterbox e Piper (opcionais, sem teste)

**Kokoro** é o padrão porque roda leve em CPU, tem vozes pt-BR e instala dentro do ambiente do app. O **Chatterbox Multilingual** exige PyTorch com CUDA e deve ficar num ambiente **separado**:

```powershell
py -3.11 -m venv chatterbox-env
.\chatterbox-env\Scripts\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.\chatterbox-env\Scripts\pip install chatterbox-tts
```

Depois, em `config.yaml`: `tts.backend: chatterbox` e `tts.chatterbox.python: <caminho>\chatterbox-env\Scripts\python.exe`. O app chama `scripts/chatterbox_worker.py` nesse Python. Para **Piper**: `tts.backend: piper`, aponte `tts.piper.model` para um `.onnx` pt-BR.

## Estrutura

```
app.py                  interface Streamlit
config.yaml, .env       configuração
setup.ps1, run.ps1      instalação e execução (Windows)
src/wifstudio/          roteiro, ComfyUI, TTS, alinhamento, legendas, render, pipeline, jobs
workflows/              workflows API JSON + mapeamento por título
models/                 voz Kokoro (e Piper, se usar)
projects/               um projeto por pasta (retomável)
outputs/                cópias dos MP4/SRT finais
tests/                  testes (servidores falsos + FFmpeg e Kokoro reais)
```

## Testes

```powershell
.\.venv\Scripts\python -m pytest tests -q
```

Os testes de ponta a ponta precisam dos modelos do Kokoro (`models/kokoro/`). Ollama e ComfyUI são simulados em `tests/fake_servers.py`.

## Problemas comuns

- **"ComfyUI inacessível"**: rode `.\run.ps1` ou abra o ComfyUI e confira o host no `.env`.
- **"O workflow ... não é compatível"**: a mensagem lista o nó ou o arquivo de modelo que falta. Ajuste o nome do arquivo no JSON do workflow ou instale o modelo.
- **Falta de VRAM (CUDA out of memory)**: reduza `image.width/height`, feche outros programas, mantenha `comfyui.free_memory_between_stages: true`.
- **Legenda com fonte errada**: coloque uma fonte instalada em `subtitles.font`.
- **Whisper não baixa**: o app segue com alinhamento proporcional e avisa. Rode uma vez com internet liberada para baixar o modelo.
