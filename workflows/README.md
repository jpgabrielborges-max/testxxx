# Workflows do ComfyUI

O app envia workflows no formato **API JSON** pela rota `/prompt` e troca só alguns valores (prompt, semente, tamanho...). Para achar o nó certo ele usa o **título** do nó, não o ID, porque os IDs mudam quando você reexporta.

## Origem (nada inventado)

Os arquivos foram **gerados pelo script `scripts/build_workflows.py`** a partir dos templates oficiais do repositório `Comfy-Org/workflow_templates` (`flux_schnell`, `video_wan2_2_5B_ti2v`), convertidos com as definições reais dos nós de um ComfyUI 0.43.0 (`/object_info`) e enviados ao validador do próprio ComfyUI, que os aceitou. Veja `PROVENANCE.md`.

| Arquivo | Uso |
|---|---|
| `flux_schnell_api.json` | Imagem. FLUX.1 schnell fp8 em checkpoint único (`flux1-schnell-fp8.safetensors` em `ComfyUI/models/checkpoints/`). |
| `flux_schnell_img2img_api.json` | Imagem com referência (continuidade entre cenas, experimental). Troca o latente vazio por `LoadImage` + `VAEEncode`. |
| `wan22_ti2v_5b_api.json` | Animação. Wan 2.2 TI2V 5B imagem-para-vídeo. No template oficial o `LoadImage` vem em bypass (o padrão é texto-para-vídeo); aqui ele está ativo. O prompt negativo é o oficial (em chinês) recomendado pelos autores do Wan. |

Arquivos de modelo esperados (nomes do template oficial):

- `ComfyUI/models/checkpoints/flux1-schnell-fp8.safetensors`
- `ComfyUI/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`
- `ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- `ComfyUI/models/vae/wan2.2_vae.safetensors`

**O que isto prova e o que não prova.** Prova que a estrutura, os tipos, as ligações e os nomes de nó existem e são aceitos pelo ComfyUI. Não prova que a geração roda com os pesos na sua GPU nem a qualidade do resultado; isso é o que `real_test.ps1` verifica.

## Atualizar

Se você atualizar o ComfyUI e algum nó mudar de forma (já aconteceu: o `SaveVideo` ganhou um combo dinâmico `format.codec`), regere os arquivos com o ComfyUI aberto:

```powershell
.\.venv\Scripts\python scripts\build_workflows.py --comfy http://127.0.0.1:8188
```

O script baixa os templates oficiais do GitHub (ou use `--templates-dir <pasta>`), converte com os nós do seu servidor e recusa gravar se algo divergir. `--check` só compara com os arquivos atuais.

## Usar o seu próprio workflow

1. Monte e teste o workflow no ComfyUI.
2. Dê um **título** claro aos nós que o app precisa mexer (duplo clique no título do nó).
3. Exporte em formato API (menu *Workflow -> Export (API)*) e salve em `workflows/`.
4. Crie `meu_workflow.mapping.json` ao lado (formato abaixo) e aponte `comfyui.workflows.*` do `config.yaml` para ele.

```json
{
  "required": ["positive_prompt", "seed", "width", "height", "filename_prefix"],
  "roles": {
    "positive_prompt": {"title": "WIF Positive Prompt", "input": "text"},
    "seed":            {"title": "WIF Sampler",         "input": "seed"},
    "width":           {"title": "WIF Latent",          "input": "width"},
    "height":          {"title": "WIF Latent",          "input": "height"},
    "filename_prefix": {"title": "WIF Save Image",      "input": "filename_prefix"}
  }
}
```

Cada papel aponta para `{title | class_type | node_id}` + o nome da entrada. O título precisa corresponder a **exatamente um** nó, senão o app para e lista os nós que encontrou, em vez de adivinhar.

Papéis: imagem (`positive_prompt`, `seed`, `width`, `height`, `filename_prefix`, `steps`), img2img (`reference_image`, `denoise`), animação (`input_image`, `prompt`, `seed`, `filename_prefix`, `width`, `height`, `length`, `fps`, `steps`).
