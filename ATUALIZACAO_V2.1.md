# Atualização v2 -> v2.1: salvamento seguro no Windows

Corrige `PermissionError: [WinError 5] Acesso negado: project.json.tmp -> project.json` (`ProjectStore.save`, `os.replace`) que derrubou a etapa de imagens no `real_test.ps1 -Steps mp4_30s`.

**Nada precisa ser reinstalado.** Ollama, ComfyUI, PyTorch, FLUX, modelos e o `.venv` continuam como estão. Não há dependência nova (só biblioteca padrão do Python).

## O que causava o erro

O `save()` antigo gravava sempre em `project.json.tmp` e trocava com `os.replace()`, sem nenhuma retentativa. No Windows, `os.replace` falha com "Acesso negado" (WinError 5) ou "arquivo em uso" (32) quando **outro programa está com o arquivo aberto naquele instante**. O app grava o `project.json` a cada cena, dezenas de vezes por minuto, então basta o antivírus (Windows Defender examina cada arquivo recém-gravado), o OneDrive (a Área de Trabalho costuma ser sincronizada) ou o indexador do Windows tocar no arquivo por alguns milissegundos.

Não consegui reproduzir o bloqueio real (não tenho Windows nem o seu antivírus). Reproduzi o **mesmo erro** simulando `os.replace` falhando com `PermissionError`: o código antigo quebra, o novo termina. A causa externa é a hipótese mais provável; o que posso afirmar é que o código antigo não tinha nenhuma defesa contra ela, e achei outros pontos fracos reais (abaixo).

## O que mudou

| Ponto | Antes | Agora |
|---|---|---|
| Arquivo temporário | nome fixo `project.json.tmp` | nome único (processo + thread + aleatório) na mesma pasta |
| Bloqueio momentâneo | falhava na primeira vez | retentativas com espera crescente e jitter, ~12 s no total |
| Falha definitiva | podia deixar lixo | `project.json` anterior **intacto**, conteúdo novo em `project.json.unsaved`, erro com instruções |
| Recuperação | nenhuma | cópia `project.json.bak`; se o principal estiver ilegível, usa a cópia |
| Threads | trava só em `save`/`load`, `list()` sem trava | uma trava por projeto em tudo, inclusive `list()` |
| Processos | nenhuma | trava de arquivo `project.json.lock` entre processos (sobra de processo morto é retomada) |
| Interface | salvava uma cópia velha do projeto e podia **apagar** o progresso da geração | só altera o projeto por `store.update()` (ler, mudar, gravar sob trava) e bloqueia opções durante tarefas |
| Log | uma falha de arquivo podia derrubar a geração | `append_log` nunca levanta erro |
| JSONs, imagens do ComfyUI, WAV, legendas | escrita direta | escrita atômica com retentativa |
| MP4 final aberto no player | falhava ao regravar | publica com sufixo de hora e segue |
| `rmtree` + `mkdir` | `mkdir` falhava se sobrasse pasta | `exist_ok=True` e remoção com retentativas |
| Pastas | só no `config.yaml` | `WIF_PROJECTS_DIR`, `WIF_OUTPUTS_DIR`, `WIF_TEST_OUTPUT_DIR` no `.env`, com `~` e `%VARIAVEIS%` |
| Diagnóstico | nenhum | `scripts\diagnose_storage.py` e etapa `armazenamento` no `real_test.ps1` |

## Quais arquivos substituir

Dentro do ZIP `what-if-local-studio-v2.1-ATUALIZACAO.zip` os caminhos já são os da sua instalação. **Copie todos de uma vez** (um depende do outro).

**Obrigatórios (o conserto):**

| Arquivo | Situação |
|---|---|
| `src/wifstudio/fsutil.py` | NOVO (escrita atômica, retentativas, travas) |
| `src/wifstudio/project.py` | substitui |
| `src/wifstudio/config.py` | substitui (pastas por variável de ambiente, `~` e `%VAR%`) |
| `src/wifstudio/pipeline.py` | substitui |
| `src/wifstudio/render.py` | substitui |
| `src/wifstudio/tts.py` | substitui |
| `src/wifstudio/subs.py` | substitui |
| `src/wifstudio/comfy.py` | substitui |
| `src/wifstudio/storage_check.py` | NOVO (autoteste de armazenamento) |
| `src/wifstudio/__init__.py` | substitui (só a versão: 0.2.1) |
| `app.py` | substitui |
| `scripts/real_check.py` | substitui (etapa `armazenamento`, pasta de testes configurável) |
| `scripts/diagnose_storage.py` | NOVO |
| `real_test.ps1` | substitui (`-OutputDir`, `-ProjectsDir`) |

**Opcionais (documentação e testes):** `README.md`, `env.example`, `.env.example`, `tests/test_storage.py` (novo), este `ATUALIZACAO_V2.1.md`.

**NÃO substitua / não mexa:** `.venv\`, `ComfyUI\` (ou o seu ComfyUI), `models\`, `.env` (o seu, com as suas configurações), `config.yaml` (se você o editou), `projects\`, `outputs\`, `real_test_output\`. O `config.yaml` e o `setup.ps1` novos só ganharam comentários e as linhas `WIF_*` vazias; **não são necessários**, porque os padrões estão no código.

### Como copiar com segurança (PowerShell)

Copia só os arquivos que estão no ZIP de atualização e guarda o que foi substituído em uma pasta de backup:

```powershell
$dest = "C:\caminho\da\sua\instalacao"        # a pasta que contem app.py
$upd  = "C:\Temp\wif_update"                  # onde voce extraiu o ZIP de atualizacao
$bk   = Join-Path $dest ("_backup_v2_" + (Get-Date -Format yyyyMMdd_HHmmss))
New-Item -ItemType Directory -Force $bk | Out-Null
foreach ($f in Get-ChildItem $upd -Recurse -File) {
  $rel = $f.FullName.Substring($upd.Length + 1)
  $target = Join-Path $dest $rel
  if (Test-Path $target) { New-Item -ItemType Directory -Force (Split-Path (Join-Path $bk $rel)) | Out-Null; Copy-Item $target (Join-Path $bk $rel) }
  New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
  Copy-Item $f.FullName $target -Force
}
"Pronto. Backup em $bk"
```

Para voltar atrás, copie o conteúdo da pasta `_backup_v2_...` de volta por cima.

## Depois de copiar

1. **Feche** o Streamlit e qualquer `real_test.ps1` em andamento.
2. (Recomendado) Escolha pastas fora da Área de Trabalho e do OneDrive. Edite o **seu** `.env` e acrescente:

   ```
   WIF_PROJECTS_DIR=C:\WIF\projects
   WIF_OUTPUTS_DIR=C:\WIF\outputs
   WIF_TEST_OUTPUT_DIR=C:\WIF\testes
   ```

   Isso não move o `.venv`, o ComfyUI nem os modelos. Projetos antigos continuam na pasta antiga; copie as subpastas de `projects\` para a nova se quiser abri-los.
3. Teste as pastas (poucos segundos):

   ```powershell
   .\.venv\Scripts\python scripts\diagnose_storage.py
   ```

   Esperado: `OK`, 240/240 gravações, 0 erros. Retentativas são normais e mostram que a proteção funcionou; elas só viram problema se o teste disser `PROBLEMA`.
4. Rode de novo o teste que falhou:

   ```powershell
   .\real_test.ps1 -Steps armazenamento,mp4_30s
   ```

   Se quiser rodar a suíte automatizada: `.\.venv\Scripts\python -m pytest tests\test_storage.py -q`.

## Se ainda aparecer "Acesso negado"

A mensagem agora diz qual arquivo e o que fazer. Envie o `relatorio.md`/`studio.log` junto com o resultado do `diagnose_storage.py`. Passos que costumam resolver: mudar `WIF_PROJECTS_DIR` para fora do OneDrive e da Área de Trabalho, ou adicionar a pasta (e `.venv`) às exclusões do Windows Defender (Segurança do Windows, Proteção contra vírus e ameaças, Exclusões). Um arquivo `project.json.unsaved` na pasta do projeto significa que um salvamento ficou preso por mais de ~12 s; o conteúdo dele está intacto.

## Limites desta correção

- Testada por simulação do erro do Windows e por concorrência real de threads e processos no Linux. O comportamento com o **seu** antivírus só se confirma rodando o `diagnose_storage.py` aí.
- Enquanto uma geração está rodando, a interface bloqueia a edição de opções e das cenas de propósito: duas partes do app salvando o mesmo projeto ao mesmo tempo seria o outro jeito de perder dados.
