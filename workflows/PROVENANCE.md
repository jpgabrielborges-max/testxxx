# Origem dos workflows

Gerados em 2026-11-01 por `scripts/build_workflows.py` a partir dos templates oficiais `flux_schnell` e `video_wan2_2_5B_ti2v` (repositório Comfy-Org/workflow_templates; a variante img2img troca o latente vazio por LoadImage+VAEEncode), convertidos com as definições reais dos nós do ComfyUI 0.43.0 e aceitos pelo validador dele.

Ajustes feitos sobre o template: títulos `WIF ...` nos nós mapeados, tamanho vertical (FLUX 768x1344; Wan 704x1280, 49 frames), nó LoadImage do Wan ativado (no template ele vem em bypass porque o padrão é texto para vídeo), seed e prompts substituídos por valores de exemplo.
