# Cinematic 2.5D — protótipo integrado (fase 1)

Módulo **opt-in** para a Content Machine. Renderiza um mundo 3D procedural com dois personagens demonstrativos articulados, gestos/eventos em uma timeline e movimento de câmera **sem cortes**. O pipeline atual com FLUX/Wan, Kokoro, FFmpeg e projetos salvos não é modificado.

**O que ele faz:** trem com peças 3D, rodas girando, árvores em profundidade, atores que viram a cabeça, piscam, simulam caminhada, reagem com medo e levantam uma lanterna. Toca mix de narração do projeto (quando existente), com SFX e legendas da timeline.

**O que NÃO faz ainda:** gerar automaticamente um rig de qualidade profissional a partir de foto; modelar cenários arbitrários para cada roteiro; resolver colisões e mãos segurando objetos de forma realista; garantia de qualidade igual a animações de estúdio; nenhum render de Remotion validado nesta máquina de desenvolvimento sem os pacotes npm.

## Instalação independente no Windows

1. Abra PowerShell na pasta `cinematic25d` (a pasta ao lado de `app.py`).
2. Instale Node.js LTS, se não tiver.
3. Execute `npm install` e depois `npm run studio` para visualizar o piloto com história de trem.
4. Abra normalmente a Content Machine com `run.ps1`. No menu lateral do Streamlit, abra **Cinematic 25D**.
5. Selecione um projeto já criado e com cenas; clique **Preparar timeline e mix de áudio**, depois **Renderizar animação 2.5D**.

Tudo roda localmente. A primeira instalação do npm precisa de acesso aos pacotes. O vídeo demonstra o motor mas os personagens são **arte procedural provisória**.

## Estrutura

- `src/wifstudio/cinematic25d.py`: importa cenas, gera movimentos por regras e sincroniza a duração da narração; exporta os props para Remotion.
- `pages/5_Cinematic_25D.py`: nova página, separada do app atual.
- `cinematic25d/src/Actors.tsx`: bonecos articulados demonstrativos.
- `cinematic25d/src/World.tsx`: trem, cenário, sombra, iluminação e câmera contínua.
- `cinematic25d/src/demo.ts`: roteiro de exemplo independente, pode testar sem Ollama ou ComfyUI.

O arquivo `props.json` é salvo dentro da pasta do projeto, em `cinematic25d/`; o mix de áudio vai para `cinematic25d/public/generated/` (ignorado pelo Git). A timeline contém **ações sugeridas por palavras-chave**, portanto deve ser revisada e corrigida manualmente quando o roteiro for mais complexo.

## Próximas etapas para qualidade premium

1. Trocar bonecos procedurais por rigs de **sprites de alta qualidade**, com peças (cabeça, tronco, braços etc.) e expressões padronizadas.
2. Criar gerenciador de bibliotecas de personagens e cenários originais.
3. Usar o Ollama para planejamento estruturado, com validação estrita de ações existentes.
4. Corrigir colisões, oclusões, contatos com objetos, sincronização fina de mãos e efeitos.
5. Automatizar QA de frames e validar renderização/tempos na RTX 5060 Ti do usuário.

## Teste automatizado da timeline

```powershell
.\.venv\Scripts\python -m pytest tests\test_cinematic25d.py -q
```

> Observação: o repositório enviado documenta proteções de salvamento da v2.1, mas o `project.py` da branch principal ainda contém o `project.json.tmp` fixo. Esta proposta não altera o salvamento existente. É recomendável restaurar a correção da v2.1 numa PR específica antes de uma implantação maior.
