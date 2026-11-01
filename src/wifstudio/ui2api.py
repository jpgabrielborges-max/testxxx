"""Converte um workflow no formato da INTERFACE do ComfyUI (os templates oficiais) para o formato API, usando as
definições REAIS dos nós do servidor (/object_info). Assim os workflows do projeto nascem dos templates oficiais em vez
de serem escritos à mão. Segue a mesma lógica de graphToPrompt do frontend: valores de widget na ordem de
INPUT_TYPES, pulando o valor extra de "control_after_generate" e do botão de upload."""
from __future__ import annotations

NON_EXECUTABLE = {"Note", "MarkdownNote"}
LINK_TYPES_PRIMITIVE = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


class ConversionError(ValueError):
    pass


DYNAMIC = "COMFY_DYNAMICCOMBO_V3"


def _is_widget(spec) -> bool:
    t = spec[0]
    return isinstance(t, list) or t in LINK_TYPES_PRIMITIVE or t == DYNAMIC


def _consume_dynamic(name: str, spec, value, values: list, vi: int, inputs: dict) -> int:
    """Combo dinâmico (ComfyUI 0.4x): o valor escolhido abre sub-entradas, gravadas como 'nome.sub' e vindas logo
    depois dele em widgets_values. Devolve o novo índice de widgets_values."""
    inputs[name] = value
    opt = next((o for o in spec[1].get("options", []) if o["key"] == value), None)
    if opt is None:
        return vi
    for group in ("required", "optional"):
        for sub, sspec in opt.get("inputs", {}).get(group, {}).items():
            if not _is_widget(sspec) or vi >= len(values):
                continue
            full = f"{name}.{sub}"
            if sspec[0] == DYNAMIC:
                vi = _consume_dynamic(full, sspec, values[vi], values, vi + 1, inputs)
            else:
                inputs[full] = values[vi]
                vi += 1 + _extra_slots(sspec)
    return vi


def _extra_slots(spec) -> int:
    """Quantos valores EXTRAS o frontend grava em widgets_values depois deste widget."""
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    n = 0
    if opts.get("control_after_generate"):
        n += 1
    if opts.get("image_upload") or opts.get("video_upload") or opts.get("audio_upload"):
        n += 1
    return n


def ui_to_api(ui: dict, object_info: dict, enable_bypassed: tuple[str, ...] = ()) -> dict:
    """enable_bypassed: tipos de nó que o template traz em bypass (modo 4) e devem ser ATIVADOS (ex.: LoadImage no Wan I2V)."""
    links = {l[0]: (str(l[1]), int(l[2])) for l in ui.get("links", [])}
    api: dict[str, dict] = {}
    for node in ui["nodes"]:
        ctype = node["type"]
        if ctype in NON_EXECUTABLE:
            continue
        mode = node.get("mode", 0)
        if mode == 4 and ctype in enable_bypassed:
            mode = 0
        if mode in (2, 4):
            raise ConversionError(f"nó {node['id']} ({ctype}) está mudo/bypass; o conversor não resolve isso. Remova-o no template.")
        info = object_info.get(ctype)
        if info is None:
            raise ConversionError(f"classe '{ctype}' não existe neste ComfyUI")
        specs = {**info["input"].get("required", {}), **info["input"].get("optional", {})}
        inputs: dict = {}
        by_name = {i["name"]: i for i in node.get("inputs", [])}
        values = list(node.get("widgets_values") or [])
        vi = 0
        optional = set(info["input"].get("optional", {}))
        for name, spec in specs.items():
            if _is_widget(spec):
                if vi >= len(values):
                    if name in optional:  # template antigo, nó novo ganhou entrada opcional: o servidor usa o padrão
                        continue
                    raise ConversionError(f"nó {node['id']} ({ctype}): faltam widgets_values para '{name}'")
                val = values[vi]
                if spec[0] == DYNAMIC:
                    vi = _consume_dynamic(name, spec, val, values, vi + 1, inputs)
                    continue
                vi += 1 + _extra_slots(spec)
                if name in by_name and by_name[name].get("link") is not None:
                    o, s = links[by_name[name]["link"]]
                    inputs[name] = [o, s]
                else:
                    inputs[name] = val
            elif name in by_name and by_name[name].get("link") is not None:
                o, s = links[by_name[name]["link"]]
                inputs[name] = [o, s]
        entry = {"class_type": ctype, "inputs": inputs, "_meta": {"title": node.get("title") or info.get("display_name") or ctype}}
        api[str(node["id"])] = entry
    return api
