"""Montagem com FFmpeg: movimento de câmera nas imagens, clipes animados, transições discretas, legendas e verificação."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Optional


class RenderError(RuntimeError):
    pass


def run_ffmpeg(args: list[str], cwd: Path | None = None, log=lambda m: None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", *args]
    log("ffmpeg " + " ".join(a if " " not in a else f'"{a}"' for a in args)[:600])
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True)
    if p.returncode != 0:
        raise RenderError(f"ffmpeg falhou (código {p.returncode}): {p.stderr.decode(errors='replace')[-1200:]}")


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True)
    if p.returncode != 0:
        raise RenderError(f"ffprobe falhou em {path}: {p.stderr.decode(errors='replace')[:300]}")
    return json.loads(p.stdout)


def media_duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def _camera_filter(move: str, frames: int, w: int, h: int, fps: int, amount: float) -> str:
    """zoompan com easing suave. A imagem entra ampliada 2x para o movimento não tremer."""
    N = max(frames, 2)
    t = f"(on/{N})"
    ease = f"({t}*{t}*(3-2*{t}))"
    z = f"{amount:.4f}"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    table = {
        "zoom_in": (f"1+{z}*{ease}", cx, cy),
        "zoom_out": (f"1+{z}*(1-{ease})", cx, cy),
        "pan_left": (f"1+{z}", f"(iw-iw/zoom)*(1-{ease})", cy),
        "pan_right": (f"1+{z}", f"(iw-iw/zoom)*{ease}", cy),
        "pan_up": (f"1+{z}", cx, f"(ih-ih/zoom)*(1-{ease})"),
        "pan_down": (f"1+{z}", cx, f"(ih-ih/zoom)*{ease}"),
        "static": ("1", cx, cy),
    }
    zz, xx, yy = table.get(move, table["zoom_in"])
    return (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase:flags=lanczos,crop={w * 2}:{h * 2},"
            f"zoompan=z='{zz}':x='{xx}':y='{yy}':d={N}:s={w}x{h}:fps={fps},format=yuv420p")


def render_scene_clip(src: Path, out: Path, duration: float, camera: str, cfg, is_video: bool = False,
                      cwd: Path | None = None, log=lambda m: None) -> Path:
    r = cfg.section("render")
    w, h, fps = r.get("width", 1080), r.get("height", 1920), r.get("fps", 30)
    frames = math.ceil(duration * fps)
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-r", str(fps), "-an"]
    if not is_video:
        vf = _camera_filter(camera, frames, w, h, fps, r.get("camera_zoom", 0.16))
        run_ffmpeg(["-i", str(src), "-vf", vf, "-frames:v", str(frames), *enc, str(out)], cwd, log)
    else:
        src_dur = media_duration(src)
        stretch = min(duration / max(src_dur, 0.1), 1.5) if src_dur < duration else 1.0
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},fps={fps},"
              f"setpts={stretch:.4f}*PTS,tpad=stop_mode=clone:stop_duration={duration:.3f},format=yuv420p")
        run_ffmpeg(["-i", str(src), "-vf", vf, "-frames:v", str(frames), *enc, str(out)], cwd, log)
    return out


def assemble(clips: list[Path], durations: list[float], audio: Path, ass: Optional[Path], out: Path, cfg,
             cwd: Path, log=lambda m: None) -> Path:
    """Encadeia os clipes com xfade discreto, queima a legenda ASS e junta o áudio mixado, em UMA codificação final.
    Cada clipe i (exceto o último) tem duração d_i + t; a transição i começa em S_i (início da cena i), então a duração
    total do vídeo é exatamente a soma das cenas e os cortes continuam alinhados às frases."""
    r = cfg.section("render")
    fps, t = r.get("fps", 30), float(r.get("transition_s", 0.18))
    kind = r.get("transition", "fade")
    n = len(clips)
    total = sum(durations)
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    inputs += ["-i", str(audio)]
    parts = [f"[{i}:v]settb=AVTB,fps={fps},setpts=PTS-STARTPTS[v{i}]" for i in range(n)]
    last = "v0"
    if n > 1 and kind != "none" and t > 0:
        start = 0.0
        for i in range(1, n):
            start += durations[i - 1]
            parts.append(f"[{last}][v{i}]xfade=transition={kind}:duration={t:.3f}:offset={start:.3f}[x{i}]")
            last = f"x{i}"
    elif n > 1:
        parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[xc]")
        last = "xc"
    post = "format=yuv420p"
    if ass is not None:
        rel = Path(ass).resolve().relative_to(Path(cwd).resolve()).as_posix()
        post = f"ass={rel},format=yuv420p"
    parts.append(f"[{last}]{post}[vout]")
    if r.get("encoder", "libx264") == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(r.get("crf", 19) + 2), "-b:v", "0"]
    else:
        venc = ["-c:v", "libx264", "-preset", r.get("preset", "veryfast"), "-crf", str(r.get("crf", 19))]
    args = [*inputs, "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", f"{n}:a",
            *venc, "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", r.get("audio_bitrate", "192k"),
            "-ar", "48000", "-ac", "2", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    run_ffmpeg(args, cwd, log)
    return out


def verify_reel(path: Path, target_s: float, tol: float = 0.15) -> dict:
    """Checagens objetivas do MP4 final (via ffprobe e volumedetect). Devolve {checks: [...], ok: bool}."""
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("container mp4", "mp4" in info["format"]["format_name"], info["format"]["format_name"])
    add("vídeo H.264", bool(v) and v["codec_name"] == "h264", v and v["codec_name"])
    add("áudio AAC", bool(a) and a["codec_name"] == "aac", a and a["codec_name"])
    add("resolução 1080x1920", bool(v) and (v["width"], v["height"]) == (1080, 1920), v and f"{v['width']}x{v['height']}")
    add("pix_fmt yuv420p", bool(v) and v.get("pix_fmt") == "yuv420p", v and v.get("pix_fmt"))
    if v:
        num, den = v["r_frame_rate"].split("/")
        fps = float(num) / float(den)
        add("30 fps", 29.5 <= fps <= 30.5, f"{fps:.2f}")
    add(f"duração perto de {target_s:g}s (±{int(tol * 100)}%)", abs(dur - target_s) <= target_s * tol, f"{dur:.2f}s")
    if v and a:
        vd, ad = float(v.get("duration", dur)), float(a.get("duration", dur))
        add("áudio e vídeo com mesma duração (±0,15 s)", abs(vd - ad) <= 0.15, f"v={vd:.2f}s a={ad:.2f}s")
    add("duração ≤ 90 s (limite de Reels)", dur <= 90, f"{dur:.2f}s")
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr.decode(errors="replace"))
    mean = float(m.group(1)) if m else -99.0
    add("narração audível (volume médio > -40 dB)", mean > -40, f"{mean:.1f} dB")
    # decodifica o arquivo inteiro para garantir que é reproduzível de ponta a ponta
    d = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True)
    add("decodifica sem erros", d.returncode == 0 and not d.stderr.strip(), d.stderr.decode(errors="replace")[:120] or "ok")
    return {"ok": all(c["ok"] for c in checks), "duration": dur, "size_mb": round(Path(path).stat().st_size / 1e6, 2),
            "mean_volume_db": mean, "checks": checks}
