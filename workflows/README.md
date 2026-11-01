# Workflows do ComfyUI

O app envia workflows no formato **API JSON** pela rota `/prompt` e troca só alguns valores (prompt, semente, tamanho...). Para achar o nó certo ele usa o **título** do nó, não o ID, porque os IDs mudam quando você reexporta.

## Arquivos

| Arquivo | Uso |
|---|---|
| `flux_schnell_api.json` + `.mapping.json` | Imagem. FLUX.1 schnell fp8 em checkpoint único (`flux1-schnell-fp8.safetensors` em `ComfyUI/models/checkpoints/`). |
| `wan22_ti2v_5b_api.json` + `.mapping.json` | Animação. Wan 2.2 TI2V 5B nos nós nativos. **Nunca foi executado**, confira com o botão *Verificar workflow Wan*. |

Os dois foram escritos sem acesso a um ComfyUI real. O app compara cada workflow com o servidor (`/object_info`) antes de rodar e lista classes de nó inexistentes e arquivos de modelo que não estão instalados.

## Como exportar o seu próprio workflow

1. No ComfyUI, monte e teste o workflow normalmente.
2. Dê um **título** claro aos nós que o app precisa mexer (duplo clique no título do nó, ou *Properties Panel → Title*).
3. Exporte em formato API: menu *Workflow → Export (API)*. Em versões antigas, habilite *Dev mode options* nas configurações e use *Save (API Format)*.
4. Salve em `workflows/meu_workflow.json`.
5. Crie `workflows/meu_workflow.mapping.json` (formato abaixo).
6. Em `config.yaml`, aponte `comfyui.workflows.image` (ou `video`) para o seu arquivo.

## Formato do mapeamento

```json
{
  "required": ["positive_prompt", "seed", "width", "height", "filename_prefix"],
  "roles": {
    "positive_prompt": {"title": "WIF Positive Prompt", "input": "text"},
    "seed":            {"title": "WIF Sampler",         "input": "seed"},
    "steps":           {"title": "WIF Sampler",         "input": "steps"},
    "width":           {"title": "WIF Latent",          "input": "width"},
    "height":          {"title": "WIF Latent",          "input": "height"},
    "filename_prefix": {"title": "WIF Save Image",      "input": "filename_prefix"}
  }
}
```

Cada papel aponta para `{title | class_type | node_id}` + o nome da entrada. O título (ou classe) precisa corresponder a **exatamente um** nó, senão o app para e lista os nós que encontrou, em vez de adivinhar.

Papéis usados:

- Imagem: `positive_prompt`, `seed`, `width`, `height`, `filename_prefix` (obrigatórios), `steps` (opcional).
- Animação: `input_image`, `prompt`, `seed`, `filename_prefix` (obrigatórios), `width`, `height`, `length`, `fps`, `steps` (opcionais).

## Se o Wan 2.2 não funcionar no seu ComfyUI

Os nomes de nó (`Wan22ImageToVideoLatent`, `CreateVideo`, `SaveVideo`) e os arquivos de modelo dependem da sua versão do ComfyUI. Abra o template oficial *Wan 2.2 5B video generation* no ComfyUI, exporte em API, renomeie os títulos como na tabela do mapeamento e substitua `wan22_ti2v_5b_api.json`. Enquanto isso, o modo rápido segue funcionando sem o Wan.
