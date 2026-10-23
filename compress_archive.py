#!/usr/bin/env python3
"""
Compress historical WAV recordings to Opus mono.

The WAV produced by Eco during capture is uncompressed PCM: voice saved at
1536 kbps. As an archive format, Opus mono at 32 kbps reduces size by ~50x
without noticeable loss in intelligibility or transcription accuracy.

By default, this tool performs a dry run. Use --apply to write files,
and --delete-originals to remove the original WAVs. An original is never
deleted without verifying that the Opus file exists, is non-empty, and its
duration matches the WAV within tolerance.

Usage:
    python compress_archive.py ~/audio_folder                  # dry run
    python compress_archive.py ~/audio_folder --apply          # convert
    python compress_archive.py ~/audio_folder --apply --delete-originals
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_BITRATE = "32k"
DURATION_TOLERANCE = 1.0  # seconds


def probe_duration(path):
    """Return duration in seconds, or None if probe fails."""
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
    """Convert a WAV file to Opus. Returns (ok, opus_path, message)."""
    opus = wav.with_suffix(".opus")

    if opus.exists():
        return False, opus, "opus already exists, skipping"

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
        return False, opus, f"ffmpeg failed: {e.stderr.decode()[:120]}"
    except Exception as e:
        if opus.exists():
            opus.unlink()
        return False, opus, f"error: {e}"

    if not opus.exists() or opus.stat().st_size == 0:
        if opus.exists():
            opus.unlink()
        return False, opus, "empty output file"

    return True, opus, ""


def verify(wav, opus):
    """Verify that Opus faithfully represents WAV before deleting the original."""
    d_wav = probe_duration(wav)
    d_opus = probe_duration(opus)

    if d_wav is None or d_opus is None:
        return False, "could not read audio duration"

    delta = abs(d_wav - d_opus)
    if delta > DURATION_TOLERANCE:
        return False, f"duration mismatch ({d_wav:.1f}s vs {d_opus:.1f}s)"

    return True, ""


def main():
    ap = argparse.ArgumentParser(
        description="Compress WAV recordings to Opus mono for archiving.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("directory", help="Folder containing .wav files (searched recursively)")
    ap.add_argument("--apply", action="store_true",
                    help="Execute conversions (dry run by default)")
    ap.add_argument("--delete-originals", action="store_true",
                    help="Delete each WAV after verifying its Opus file. Requires --apply")
    ap.add_argument("--bitrate", default=DEFAULT_BITRATE,
                    help=f"Opus audio bitrate (default: {DEFAULT_BITRATE})")
    ap.add_argument("--manifest", metavar="FILE",
                    help="Save JSON summary report of all conversions")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        sys.exit("ffmpeg/ffprobe not found. Install with: brew install ffmpeg")

    root = Path(args.directory).expanduser()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

    wavs = sorted(p for p in root.rglob("*.wav") if p.is_file())
    if not wavs:
        sys.exit(f"No .wav files found in {root}")

    total_before = sum(p.stat().st_size for p in wavs)
    print(f"\n  {len(wavs)} WAV files, {human(total_before)} total")
    print(f"  Target: Opus mono {args.bitrate}")
    if not args.apply:
        print("  SIMULATION MODE - nothing will be modified (use --apply to execute)")
    elif args.delete_originals:
        print("  WAV files will be deleted after verifying each Opus file")
    print()

    results = []
    total_after = 0
    converted = skipped = failed = deleted = 0

    for i, wav in enumerate(wavs, 1):
        size_before = wav.stat().st_size
        label = f"[{i}/{len(wavs)}] {wav.name[:52]}"

        if not args.apply:
            est = size_before / 54  # measured ratio for 32k mono voice
            print(f"  {label}\n      {human(size_before)} -> ~{human(est)} (estimated)")
            total_after += est
            continue

        ok, opus, msg = convert(wav, args.bitrate)
        if not ok:
            print(f"  {label}\n      SKIPPED: {msg}")
            skipped += 1
            total_after += size_before
            results.append({"wav": str(wav), "status": "skipped", "reason": msg})
            continue

        good, why = verify(wav, opus)
        if not good:
            print(f"  {label}\n      VERIFICATION FAILED: {why} - keeping WAV")
            opus.unlink()
            failed += 1
            total_after += size_before
            results.append({"wav": str(wav), "status": "failed", "reason": why})
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
              f"({ratio:.0f}x){'  [WAV deleted]' if removed else ''}")

        results.append({
            "wav": str(wav), "opus": str(opus), "status": "ok",
            "bytes_before": size_before, "bytes_after": size_after,
            "ratio": round(ratio, 1), "wav_deleted": removed,
        })

    print(f"\n  {'—' * 52}")
    print(f"  Before:  {human(total_before)}")
    print(f"  After:   {human(total_after)}")
    if total_after:
        print(f"  Savings: {human(total_before - total_after)} "
              f"({total_before / total_after:.0f}x smaller)")
    if args.apply:
        print(f"\n  Converted: {converted}   Skipped: {skipped}   "
              f"Failed: {failed}   WAV deleted: {deleted}")
        if failed or skipped:
            print("  Original WAVs of skipped or failed files were kept intact.")
    else:
        print("\n  Nothing was modified. Add --apply to execute.")

    if args.manifest and args.apply:
        Path(args.manifest).write_text(json.dumps({
            "date": datetime.now().isoformat(),
            "directory": str(root),
            "bitrate": args.bitrate,
            "bytes_before": total_before,
            "bytes_after": total_after,
            "files": results,
        }, indent=2, ensure_ascii=False))
        print(f"  Manifest written to: {args.manifest}")

    print()


if __name__ == "__main__":
    main()
