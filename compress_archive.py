#!/usr/bin/env python3
"""
Comprime un archivo histórico de grabaciones WAV a Opus mono.

El WAV que produce Eco durante la captura es PCM sin comprimir: voz guardada a
1536 kbps. Como archivo, Opus mono a 32 kbps la reduce unas 50x sin perder
inteligibilidad ni precisión de transcripción.

Por defecto solo simula (--dry-run implícito). Para escribir hay que pasar
--apply, y para borrar los WAV originales, --delete-originals. Un original nunca
se borra sin antes verificar que el Opus existe, no está vacío y su duración
coincide con la del WAV.

Uso:
    python compress_archive.py ~/ruta/audio                 # simula
    python compress_archive.py ~/ruta/audio --apply         # convierte
    python compress_archive.py ~/ruta/audio --apply --delete-originals
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_BITRATE = "32k"
DURATION_TOLERANCE = 1.0  # segundos


def probe_duration(path):
    """Duración en segundos, o None si no se puede leer."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True, timeout=120,
        )
        return float(res.stdout.strip())
    except Exception:
        return None


def human(nbytes):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(nbytes) < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024


def convert(wav, bitrate):
    """Convierte un WAV a Opus. Devuelve (ok, ruta_opus, mensaje)."""
    opus = wav.with_suffix(".opus")

    if opus.exists():
        return False, opus, "ya existe un .opus, se omite"

    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y",
             "-i", str(wav),
             "-c:a", "libopus", "-b:a", bitrate, "-ac", "1",
             str(opus)],
            check=True, capture_output=True, timeout=3600,
        )
    except subprocess.CalledProcessError as e:
        if opus.exists():
            opus.unlink()
        return False, opus, f"ffmpeg falló: {e.stderr.decode()[:120]}"
    except Exception as e:
        if opus.exists():
            opus.unlink()
        return False, opus, f"error: {e}"

    if not opus.exists() or opus.stat().st_size == 0:
        if opus.exists():
            opus.unlink()
        return False, opus, "resultado vacío"

    return True, opus, ""


def verify(wav, opus):
    """Comprueba que el Opus representa fielmente al WAV antes de borrar nada."""
    d_wav = probe_duration(wav)
    d_opus = probe_duration(opus)

    if d_wav is None or d_opus is None:
        return False, "no se pudo leer la duración"

    delta = abs(d_wav - d_opus)
    if delta > DURATION_TOLERANCE:
        return False, f"duración distinta ({d_wav:.1f}s vs {d_opus:.1f}s)"

    return True, ""


def main():
    ap = argparse.ArgumentParser(
        description="Comprime grabaciones WAV a Opus mono para archivado.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("directorio", help="Carpeta con los .wav (se recorre recursivamente)")
    ap.add_argument("--apply", action="store_true",
                    help="Escribe de verdad (sin este flag solo simula)")
    ap.add_argument("--delete-originals", action="store_true",
                    help="Borra cada WAV tras verificar su Opus. Requiere --apply")
    ap.add_argument("--bitrate", default=DEFAULT_BITRATE,
                    help=f"Bitrate del Opus (default: {DEFAULT_BITRATE})")
    ap.add_argument("--manifest", metavar="FILE",
                    help="Guarda un JSON con el resultado de cada archivo")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        sys.exit("ffmpeg/ffprobe no encontrados. Instala con: brew install ffmpeg")

    root = Path(args.directorio).expanduser()
    if not root.is_dir():
        sys.exit(f"No es un directorio: {root}")

    wavs = sorted(p for p in root.rglob("*.wav") if p.is_file())
    if not wavs:
        sys.exit(f"No se encontraron .wav en {root}")

    total_before = sum(p.stat().st_size for p in wavs)
    print(f"\n  {len(wavs)} archivos WAV, {human(total_before)} en total")
    print(f"  Destino: Opus mono {args.bitrate}")
    if not args.apply:
        print("  MODO SIMULACIÓN — no se escribe nada (usa --apply para ejecutar)")
    elif args.delete_originals:
        print("  Los WAV se borrarán tras verificar cada Opus")
    print()

    results = []
    total_after = 0
    converted = skipped = failed = deleted = 0

    for i, wav in enumerate(wavs, 1):
        size_before = wav.stat().st_size
        label = f"[{i}/{len(wavs)}] {wav.name[:52]}"

        if not args.apply:
            est = size_before / 54  # ratio medido para voz a 32k mono
            print(f"  {label}\n      {human(size_before)} -> ~{human(est)} (estimado)")
            total_after += est
            continue

        ok, opus, msg = convert(wav, args.bitrate)
        if not ok:
            print(f"  {label}\n      OMITIDO: {msg}")
            skipped += 1
            total_after += size_before
            results.append({"wav": str(wav), "estado": "omitido", "motivo": msg})
            continue

        good, why = verify(wav, opus)
        if not good:
            print(f"  {label}\n      FALLO DE VERIFICACIÓN: {why} — se conserva el WAV")
            opus.unlink()
            failed += 1
            total_after += size_before
            results.append({"wav": str(wav), "estado": "fallo", "motivo": why})
            continue

        size_after = opus.stat().st_size
        total_after += size_after
        converted += 1
        ratio = size_before / size_after

        removed = False
        if args.delete_originals:
            wav.unlink()
            removed = True
            deleted += 1

        print(f"  {label}\n      {human(size_before)} -> {human(size_after)} "
              f"({ratio:.0f}x){'  [WAV borrado]' if removed else ''}")

        results.append({
            "wav": str(wav), "opus": str(opus), "estado": "ok",
            "bytes_antes": size_before, "bytes_despues": size_after,
            "ratio": round(ratio, 1), "wav_borrado": removed,
        })

    print(f"\n  {'—' * 52}")
    print(f"  Antes:   {human(total_before)}")
    print(f"  Después: {human(total_after)}")
    if total_after:
        print(f"  Ahorro:  {human(total_before - total_after)} "
              f"({total_before / total_after:.0f}x menos)")
    if args.apply:
        print(f"\n  Convertidos: {converted}   Omitidos: {skipped}   "
              f"Fallidos: {failed}   WAV borrados: {deleted}")
        if failed or skipped:
            print("  Los WAV de los archivos omitidos o fallidos siguen intactos.")
    else:
        print("\n  Nada se ha modificado. Añade --apply para ejecutar.")

    if args.manifest and args.apply:
        Path(args.manifest).write_text(json.dumps({
            "fecha": datetime.now().isoformat(),
            "directorio": str(root),
            "bitrate": args.bitrate,
            "bytes_antes": total_before,
            "bytes_despues": total_after,
            "archivos": results,
        }, indent=2, ensure_ascii=False))
        print(f"  Manifiesto: {args.manifest}")

    print()


if __name__ == "__main__":
    main()
