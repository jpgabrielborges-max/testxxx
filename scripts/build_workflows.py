"""Gera os workflows API do projeto A PARTIR DOS TEMPLATES OFICIAIS do ComfyUI (Comfy-Org/workflow_templates),
usando as definições reais dos nós do seu ComfyUI em execução. Nada de nó inventado.

  python scripts/build_workflows.py [--comfy http://127.0.0.1:8188] [--templates-dir PASTA] [--check]

Saída: workflows/flux_schnell_api.json, flux_schnell_img2img_api.json e wan22_ti2v_5b_api.json (+ PROVENANCE.md).
Depois de gerar, cada workflow é enviado ao /prompt do ComfyUI para validação de tipos, ligações e nomes de modelo.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wifstudio.comfy import ComfyClient, ComfyError, validate_against_server  # noqa: E402
from wifstudio.ui2api import ui_to_api  # noqa: E402

RAW = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/{}.json"


def fetch_template(name: str, local_dir: Path | None) -> dict:
    if local_dir and (local_dir / f"{name}.json").exists():
        return json.loads((local_dir / f"{name}.json").read_text(encoding="utf-8"))
    with urllib.request.urlopen(RAW.format(name), timeout=60) as r:  # noqa: S310 - URL fixa do repositório oficial
        return json.loads(r.read())


def linked_to(api: dict, sampler_id: str, input_name: str) -> str:
    return str(api[sampler_id]["inputs"][input_name][0])


def one(api: dict, ctype: str) -> str:
    ids = [i for i, n in api.items() if n["class_type"] == ctype]
    if len(ids) != 1:
        raise SystemExit(f"esperava 1 nó {ctype} no template, achei {len(ids)}: o template oficial mudou. Revise este script.")
    return ids[0]


def retitle(api: dict, titles: dict[str, str]) -> None:
    for nid, t in titles.items():
        api[nid]["_meta"]["title"] = t


def build_flux(api: dict) -> dict:
    ks = one(api, "KSampler")
    retitle(api, {
        one(api, "CheckpointLoaderSimple"): "WIF Checkpoint", linked_to(api, ks, "positive"): "WIF Positive Prompt",
        linked_to(api, ks, "negative"): "WIF Negative Prompt", one(api, "EmptySD3LatentImage"): "WIF Latent",
        ks: "WIF Sampler", one(api, "VAEDecode"): "WIF Decode", one(api, "SaveImage"): "WIF Save Image"})
    lat = api[one(api, "EmptySD3LatentImage")]["inputs"]
    lat["width"], lat["height"] = 768, 1344  # 9:16 para Reels
    api[ks]["inputs"]["seed"] = 1
    api[linked_to(api, ks, "positive")]["inputs"]["text"] = "PLACEHOLDER"
    api[one(api, "SaveImage")]["inputs"]["filename_prefix"] = "wif/test"
    return api


def build_flux_img2img(flux_api: dict) -> dict:
    """Variante com imagem de referência (continuidade entre cenas): troca o latente vazio por LoadImage -> VAEEncode, com denoise < 1.
    Usa só nós padrão do ComfyUI (LoadImage, VAEEncode); o resultado é validado no servidor como os demais."""
    import copy
    api = copy.deepcopy(flux_api)
    ks, ckpt = one(api, "KSampler"), one(api, "CheckpointLoaderSimple")
    del api[one(api, "EmptySD3LatentImage")]
    nid = max(int(i) for i in api) + 1
    load, enc = str(nid), str(nid + 1)
    api[load] = {"class_type": "LoadImage", "inputs": {"image": "example.png"}, "_meta": {"title": "WIF Reference Image"}}
    api[enc] = {"class_type": "VAEEncode", "inputs": {"pixels": [load, 0], "vae": [ckpt, 2]}, "_meta": {"title": "WIF Encode"}}
    api[ks]["inputs"]["latent_image"] = [enc, 0]
    api[ks]["inputs"]["denoise"] = 0.72
    return api


def build_wan(api: dict) -> dict:
    ks = one(api, "KSampler")
    retitle(api, {
        one(api, "UNETLoader"): "WIF Wan UNET", one(api, "CLIPLoader"): "WIF Wan CLIP", one(api, "VAELoader"): "WIF Wan VAE",
        one(api, "ModelSamplingSD3"): "WIF Model Sampling", linked_to(api, ks, "positive"): "WIF Video Prompt",
        linked_to(api, ks, "negative"): "WIF Video Negative", one(api, "LoadImage"): "WIF Input Image",
        one(api, "Wan22ImageToVideoLatent"): "WIF Video Latent", ks: "WIF Video Sampler",
        one(api, "VAEDecode"): "WIF Video Decode", one(api, "CreateVideo"): "WIF Create Video", one(api, "SaveVideo"): "WIF Save Video"})
    lat = api[one(api, "Wan22ImageToVideoLatent")]["inputs"]
    lat["width"], lat["height"], lat["length"] = 704, 1280, 49  # retrato 9:16; length precisa ser 4n+1
    api[ks]["inputs"]["seed"] = 1
    api[linked_to(api, ks, "positive")]["inputs"]["text"] = "PLACEHOLDER"
    api[one(api, "SaveVideo")]["inputs"]["filename_prefix"] = "wif/video"
    return api


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--templates-dir", default=None)
    ap.add_argument("--out", default=str(ROOT / "workflows"))
    ap.add_argument("--check", action="store_true", help="não grava; só compara o resultado com os arquivos atuais")
    a = ap.parse_args()
    client = ComfyClient(a.comfy)
    try:
        stats = client.system_stats()
        info = client.object_info()
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO: ComfyUI inacessível em {a.comfy}: {exc}. Inicie o ComfyUI e rode de novo.")
        return 2
    version = stats.get("system", {}).get("comfyui_version", "?")
    tdir = Path(a.templates_dir) if a.templates_dir else None
    jobs = [("flux_schnell", "flux_schnell_api.json", build_flux, ()),
            ("flux_schnell", "flux_schnell_img2img_api.json", lambda api: build_flux_img2img(build_flux(api)), ()),
            ("video_wan2_2_5B_ti2v", "wan22_ti2v_5b_api.json", build_wan, ("LoadImage",))]
    out_dir = Path(a.out)
    rc = 0
    for template, fname, builder, enable in jobs:
        ui = fetch_template(template, tdir)
        api = builder(ui_to_api(ui, info, enable_bypassed=enable))
        probs = validate_against_server(api, info)
        print(f"[{template}] {len(api)} nós | divergências com o servidor: {probs or 'nenhuma'}")
        if probs:
            rc = 1
            continue
        text = json.dumps(api, ensure_ascii=False, indent=2) + "\n"
        target = out_dir / fname
        if a.check:
            same = target.exists() and json.loads(target.read_text(encoding="utf-8")) == api
            print(f"   {'igual ao arquivo atual' if same else 'DIFERENTE do arquivo atual'}")
            rc = rc or (0 if same else 1)
            continue
        target.write_text(text, encoding="utf-8")
        print(f"   gravado {target}")
    if not a.check and rc == 0:
        (out_dir / "PROVENANCE.md").write_text(
            f"# Origem dos workflows\n\nGerados em {date.today()} por `scripts/build_workflows.py` a partir dos templates oficiais "
            f"`flux_schnell` e `video_wan2_2_5B_ti2v` (repositório Comfy-Org/workflow_templates; a variante img2img troca o latente vazio por LoadImage+VAEEncode), convertidos com as definições reais "
            f"dos nós do ComfyUI {version} e aceitos pelo validador dele.\n\nAjustes feitos sobre o template: títulos `WIF ...` nos nós "
            f"mapeados, tamanho vertical (FLUX 768x1344; Wan 704x1280, 49 frames), nó LoadImage do Wan ativado (no template ele vem em "
            f"bypass porque o padrão é texto para vídeo), seed e prompts substituídos por valores de exemplo.\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
