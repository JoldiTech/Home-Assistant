#!/usr/bin/env python3
"""Pull one day of Tea One audio straight from UniFi Protect and transcribe it
with faster-whisper large-v3 on the AI box's GPU.

This is the STANDING source for the store's Captain's Log. It replaces the old
local whisper add-ons on the HA box: the nightly Captain's Log job SSHes to the
AI box and runs this, then summarizes + de-identifies the output.

  ssh aibox 'source ~/transcribe-env/bin/activate && \
             python3 ~/transcribe/transcribe_day.py 2026-07-20'

Reads UniFi Protect creds from /etc/nmteaco/protect.env (never hardcoded):
  PROTECT_HOST, PROTECT_LOCAL_USER, PROTECT_LOCAL_PASS

Writes the transcript to  ~/captains_transcripts/tea_one_<date>.log  and also
prints it to stdout (the caller reads either). Exit code is 0 even for an empty
day (store closed / no speech) - the caller decides what to do with zero lines.

Hallucination-loop fix carried over from validation: condition_on_previous_text
is OFF and VAD is stricter, so ambiguous audio can't spiral into repeated
phrases. Timestamps stay mapped to the original (untrimmed) audio.
"""
import argparse
import asyncio
import gc
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from uiprotect import ProtectApiClient
from faster_whisper import WhisperModel

TZ = ZoneInfo("America/Denver")
CAM_ID = "6a59918c01154803e4003451"  # Tea One
CHANNEL = 2  # low-res 640x360 - we only need the audio track
# Business-day envelope. VAD produces nothing during closed/silent stretches, so
# a wide window is safe; the end is also capped at "now" so we never ask Protect
# for footage that doesn't exist yet (the nightly job runs ~7pm).
START_HOUR = int(os.environ.get("TRANSCRIBE_START_HOUR", "8"))
END_HOUR = int(os.environ.get("TRANSCRIBE_END_HOUR", "20"))

MAX_REPEAT = 2  # collapse a line repeated more than this many times in a row
VAD_PARAMS = dict(threshold=0.6, min_silence_duration_ms=500)

# ASR engine: "whisper" (default, unchanged), "parakeet", or "dual".
#
# Measured head-to-head on this shop's audio (2026-07-25). Over ten minutes of
# empty closing time Whisper produced "Thank you for watching.", "I love you."
# and "Thank you." from silence, in the three chunks where Parakeet returned
# NOTHING; on real speech Parakeet was also the more intelligible of the two
# ("I'm looking for a lower caffeine for the tea" vs Whisper's "it's a purchase
# caffeine green tea"). A transducer cannot generate prose from noise the way an
# autoregressive decoder must, which is the whole point.
#
# But Parakeet is not a strict superset: on one busy chunk it returned empty
# where Whisper found 258 characters of real conversation. So "dual" keeps
# Parakeet as the backbone and uses Whisper only to rescue windows Parakeet
# missed - never to overrule it.
ASR_ENGINE = os.environ.get("ASR_ENGINE", "whisper")
PARAKEET_MODEL = os.path.expanduser(
    os.environ.get("PARAKEET_MODEL", "~/transcribe/models/parakeet.nemo"))
# Parakeet decodes a whole file in one pass and OOMs past ~2 min on the 6GB card.
PARAKEET_CHUNK_S = int(os.environ.get("PARAKEET_CHUNK_S", "120"))
# What separates a Whisper hallucination from a window Parakeet genuinely missed
# is ISOLATION, not length. The first cut of this used a >=60 char threshold and
# it was wrong: over one day it suppressed 31 windows, most of them real short
# speech ("Yeah, have a good one.", "Well, it's in the back of the drawer.").
# Real conversation is full of short utterances. What the confabulations had in
# common was being marooned - alone in a 19-minute dead stretch - while genuine
# short lines sit next to other speech. So a Whisper-only window is rescued when
# Parakeet heard anything nearby, and suppressed only when nothing did.
WHISPER_RESCUE_NEAR_S = int(os.environ.get("WHISPER_RESCUE_NEAR_S", "240"))
# Domain vocabulary injected into every decode window (hotwords work with
# condition_on_previous_text=False; initial_prompt would only reach the first
# window). Whisper mishears tea terms it doesn't know - "pu-erh" became "poire",
# "Monk's" became "Munch's". Kept deliberately small: overstuffed hotwords
# degrade decoding.
HOTWORDS = (
    "pu-erh, rooibos, honeybush, oolong, genmaicha, sencha, gyokuro, matcha, "
    "hojicha, tulsi, yerba mate, guayusa, Nilgiri, Darjeeling, Assam, Keemun, "
    "Ceylon, lapsang souchong, Earl Grey, Monk's Blend, silver needle, "
    "white peony, Tieguanyin, masala chai, hibiscus, chamomile, elderberry, "
    "lemongrass, tisane, Wenshan, tulsi Krishna, Rama, Vana"
)
_NOISE = {"[blank_audio]", "(blank_audio)", "[silence]", "[music]", "(music)", "you", "."}

# Whisper's canonical silence-hallucinations. Its training data is full of video
# outros, so given near-silence it emits these almost verbatim - the shop's
# empty closing hour produced "Thank you for watching, and I'll see you in the
# next video."
#
# Deliberately phrase-specific. The obvious-looking filter here is segment
# no_speech_prob, and it does NOT work on this mic: measured over a busy ten
# minutes, real speech runs p50 0.63 / p90 0.65 while the hallucination scored
# 0.64. A 0.6 cut dropped 26 of 51 genuine segments ("I know, it's the pretzel
# stuff"). The signal is too marginal for the model's own confidence to
# separate speech from silence, so only unmistakable phrases are removed here.
# A bare "thank you" is NOT matched - customers say it constantly.
_HALLUCINATION_RE = re.compile(
    r"(thanks?\s+(you\s+)?for\s+watching"
    r"|in\s+(the\s+next|this)\s+video"          # "In the next video, we will learn..."
    r"|see\s+you\s+(in|on)\s+the\s+next"
    r"|please\s+(like\s+and\s+)?subscribe"
    r"|subscribe\s+to\s+(my|the|our)\s+channel"
    r"|don'?t\s+forget\s+to\s+(like|subscribe)"
    r"|hit\s+the\s+like\s+button"
    r"|welcome\s+back\s+to\s+(my|the|our)\s+channel)",
    re.I,
)

# The 6GB card is shared with Chloe (imagegen), whose SDXL pass can hold ~4GB.
# large-v3 float16 needs ~4GB, so a badly-timed overlap OOMs mid-run. The
# summarizer already degrades gracefully via its [30,20,0] layer chain; this
# gives the transcriber the same courtesy instead of dying. Order matters:
# float16 is the quality baseline, int8_float16 roughly halves the weights.
_LOAD_PLAN = (
    dict(device="cuda", compute_type="float16"),
    dict(device="cuda", compute_type="int8_float16"),
    dict(device="cpu", compute_type="int8"),
)
_OOM_MARKERS = ("out of memory", "cuda failed", "cublas", "cudnn")


def _is_oom(e: Exception) -> bool:
    return any(m in str(e).lower() for m in _OOM_MARKERS)


def _free_vram_mb() -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return int(r.stdout.split("\n")[0].strip())
    except Exception:
        return -1


def _load_model(plan_from: int = 0):
    """Load large-v3, degrading through _LOAD_PLAN on VRAM failure."""
    last = None
    for i in range(plan_from, len(_LOAD_PLAN)):
        cfg = _LOAD_PLAN[i]
        try:
            print(f"[transcribe_day] loading large-v3 ({cfg['device']}/{cfg['compute_type']}, "
                  f"{_free_vram_mb()}MB VRAM free)...", file=sys.stderr, flush=True)
            return WhisperModel("large-v3", **cfg), i
        except Exception as e:
            last = e
            print(f"[transcribe_day] load failed ({cfg['compute_type']}): {e}",
                  file=sys.stderr, flush=True)
            if not _is_oom(e) and cfg["device"] == "cpu":
                break
    raise RuntimeError(f"could not load large-v3 in any mode: {last}")

OUT_DIR = Path.home() / "captains_transcripts"
TMP = Path("/tmp/transcribe_day")


# --- Parakeet (NeMo) ----------------------------------------------------------

def _load_parakeet():
    """Import NeMo lazily - it is heavy and only needed for the parakeet paths.
    numpy is pinned <2.5 in this venv because NeMo's numba refuses 2.5."""
    from nemo.collections.asr.models import EncDecRNNTBPEModel
    print(f"[transcribe_day] loading parakeet ({_free_vram_mb()}MB VRAM free)...",
          file=sys.stderr, flush=True)
    m = EncDecRNNTBPEModel.restore_from(PARAKEET_MODEL, map_location="cuda")
    m.eval()
    return m


def _split_wav(wav: Path, seconds: int) -> list[tuple[Path, float]]:
    """Cut a wav into fixed-length pieces, returning (path, offset_seconds)."""
    outdir = wav.parent / f"{wav.stem}_seg"
    if outdir.exists():
        for p in outdir.glob("*.wav"):
            p.unlink()
    outdir.mkdir(exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
         "-f", "segment", "-segment_time", str(seconds), "-c", "copy",
         str(outdir / "s_%04d.wav")],
        check=True,
    )
    segs = sorted(outdir.glob("s_*.wav"))
    return [(p, i * seconds) for i, p in enumerate(segs)]


def _parakeet_lines(model, wav: Path, base: datetime) -> list[tuple[datetime, str]]:
    """Transcribe one hour with Parakeet, chunked, with real timestamps."""
    pieces = _split_wav(wav, PARAKEET_CHUNK_S)
    if not pieces:
        return []
    out = model.transcribe([str(p) for p, _ in pieces], batch_size=1, timestamps=True)
    if isinstance(out, tuple):
        out = out[0]
    lines = []
    for (path, offset), hyp in zip(pieces, out):
        ts = getattr(hyp, "timestamp", None) or {}
        segs = ts.get("segment") or []
        if segs:
            for s in segs:
                txt = (s.get("segment") or "").strip()
                if txt:
                    lines.append((base + timedelta(seconds=offset + float(s["start"])), txt))
        else:  # no timestamps came back - fall back to the chunk's own start
            txt = (hyp if isinstance(hyp, str) else getattr(hyp, "text", "")).strip()
            if txt:
                lines.append((base + timedelta(seconds=offset), txt))
        path.unlink(missing_ok=True)
    return lines


def _clean(text: str, prev: str, run: int) -> tuple[str | None, str, int]:
    """Shared line hygiene: noise words, repeat collapsing, video-isms."""
    text = text.strip()
    if not text or text.lower() in _NOISE:
        return None, prev, run
    if _HALLUCINATION_RE.search(text):
        print(f"[transcribe_day] dropped silence-hallucination: {text[:60]}",
              file=sys.stderr, flush=True)
        return None, prev, run
    if text == prev:
        run += 1
        if run > MAX_REPEAT:
            return None, prev, run
    else:
        prev, run = text, 1
    return text, prev, run


def _merge_dual(pk: list[tuple[datetime, str]],
                wh: list[tuple[datetime, str]]) -> tuple[list[tuple[datetime, str]], dict]:
    """Parakeet is the transcript; Whisper only fills windows Parakeet missed.

    Whisper never overrules Parakeet where both heard speech - measured, Parakeet
    is the cleaner of the two on real conversation, and its weaker product names
    are corrected downstream against the 3dcart catalog. Whisper earns a window
    only when Parakeet returned nothing AND the window holds enough text to be a
    real exchange rather than a confabulated one-liner.
    """
    import bisect
    win = timedelta(seconds=PARAKEET_CHUNK_S)

    def bucket(t: datetime) -> int:
        return int(t.timestamp() // win.total_seconds())

    spoken = {bucket(t) for t, _ in pk}
    pk_times = sorted(t.timestamp() for t, _ in pk)

    def parakeet_nearby(t: datetime) -> bool:
        """Did Parakeet hear anything within WHISPER_RESCUE_NEAR_S either side?"""
        if not pk_times:
            return False
        x = t.timestamp()
        i = bisect.bisect_left(pk_times, x)
        for j in (i - 1, i):
            if 0 <= j < len(pk_times) and abs(pk_times[j] - x) <= WHISPER_RESCUE_NEAR_S:
                return True
        return False

    by_win: dict[int, list[tuple[datetime, str]]] = {}
    for t, txt in wh:
        by_win.setdefault(bucket(t), []).append((t, txt))

    rescued, suppressed = [], 0
    for b, items in sorted(by_win.items()):
        if b in spoken:
            continue                                  # Parakeet already has it
        if parakeet_nearby(items[0][0]):
            rescued.extend(items)                     # a miss inside an active stretch
        else:
            suppressed += len(items)
            for _, t in items:
                print(f"[transcribe_day] suppressed whisper-only fragment "
                      f"(no parakeet speech within {WHISPER_RESCUE_NEAR_S}s): {t[:60]}",
                      file=sys.stderr, flush=True)
    merged = sorted(pk + rescued, key=lambda x: x[0])
    return merged, {"parakeet": len(pk), "rescued": len(rescued),
                    "suppressed": suppressed}


def _load_creds():
    # /etc/nmteaco/protect.env is `KEY=value` lines (mode 600). Parse without a
    # shell so a child process doesn't need it exported.
    creds = {}
    with open("/etc/nmteaco/protect.env") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
    return creds


def _now_denver():
    # Date.now() is fine here (real script on the box, not a workflow sandbox).
    return datetime.now(TZ)


def _windows(day: datetime):
    start = day.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    end = day.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
    end = min(end, _now_denver())  # never pull future footage
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(hours=1), end)
        yield cur, nxt
        cur = nxt


async def _export_hours(client, day: datetime) -> list[tuple[Path, datetime, datetime]]:
    """Pull every hour of the day to wav and KEEP them - both engines need the
    same audio, and they cannot be resident together on a 6GB card (large-v3
    ~4GB + parakeet ~2.4GB), so the day is walked twice."""
    out = []
    for i, (start, end) in enumerate(_windows(day)):
        mp4 = TMP / f"chunk_{i}.mp4"
        wav = TMP / f"chunk_{i}.wav"
        t0 = time.time()
        try:
            await client.get_camera_video(CAM_ID, start, end, channel_index=CHANNEL,
                                          output_file=mp4)
        except Exception as e:
            print(f"[transcribe_day] chunk {i} export failed: {e}", file=sys.stderr, flush=True)
            continue
        if not mp4.exists() or mp4.stat().st_size == 0:
            continue
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
             "-map", "0:a:0", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        mp4.unlink(missing_ok=True)
        out.append((wav, start, end))
        print(f"[transcribe_day] exported {start:%H:%M}-{end:%H:%M} ({time.time()-t0:.0f}s)",
              file=sys.stderr, flush=True)
    return out


async def run_dual(client, day: datetime) -> tuple[list[str], dict]:
    """Parakeet backbone + Whisper rescue. Returns formatted lines and stats."""
    hours = await _export_hours(client, day)
    if not hours:
        return [], {}

    # --- pass 1: parakeet over everything (fast: RTF ~0.007) ---
    pk_all: list[tuple[datetime, str]] = []
    pk = _load_parakeet()
    t0 = time.time()
    for wav, start, end in hours:
        try:
            got = _parakeet_lines(pk, wav, start)
        except Exception as e:
            print(f"[transcribe_day] parakeet {start:%H:%M} failed: {e}",
                  file=sys.stderr, flush=True)
            got = []
        pk_all.extend(got)
        print(f"[transcribe_day] parakeet {start:%H:%M}-{end:%H:%M} -> {len(got)} lines",
              file=sys.stderr, flush=True)
    print(f"[transcribe_day] parakeet pass: {len(pk_all)} lines in {time.time()-t0:.0f}s",
          file=sys.stderr, flush=True)
    del pk
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass

    if ASR_ENGINE == "parakeet":
        wh_all: list[tuple[datetime, str]] = []
    else:
        # --- pass 2: whisper, purely as a safety net for what parakeet missed ---
        wmodel, plan_i = _load_model()
        wh_all = []
        t0 = time.time()
        for wav, start, end in hours:
            try:
                segs, _ = wmodel.transcribe(
                    str(wav), language="en", vad_filter=True, vad_parameters=VAD_PARAMS,
                    condition_on_previous_text=False, hotwords=HOTWORDS)
                for s in segs:
                    if s.text.strip():
                        wh_all.append((start + timedelta(seconds=s.start), s.text.strip()))
            except Exception as e:
                print(f"[transcribe_day] whisper {start:%H:%M} failed: {e}",
                      file=sys.stderr, flush=True)
        print(f"[transcribe_day] whisper pass: {len(wh_all)} lines in {time.time()-t0:.0f}s",
              file=sys.stderr, flush=True)

    for wav, _, _ in hours:
        wav.unlink(missing_ok=True)
        segdir = wav.parent / f"{wav.stem}_seg"
        if segdir.exists():
            for p in segdir.glob("*.wav"):
                p.unlink(missing_ok=True)
            segdir.rmdir()

    # Keep each engine's raw output. Re-tuning the merge rule against a stored
    # pair costs seconds; without these it costs a ~30 min re-transcription per
    # day, which is how the first (wrong) rule survived long enough to ship.
    stamp = day.strftime("%Y-%m-%d")
    side = OUT_DIR / "engine_raw"
    side.mkdir(exist_ok=True)
    for name, rows in (("parakeet", pk_all), ("whisper", wh_all)):
        (side / f"tea_one_{stamp}.{name}.log").write_text(
            "".join(f"{t.strftime('%Y-%m-%d %H:%M:%S %Z')} | {x}\n" for t, x in sorted(rows)))

    merged, stats = _merge_dual(pk_all, wh_all) if wh_all else (sorted(pk_all), {
        "parakeet": len(pk_all), "rescued": 0, "suppressed": 0})

    lines, prev, run = [], None, 0
    for ts, text in merged:
        text, prev, run = _clean(text, prev, run)
        if text:
            lines.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S %Z')} | {text}")
    stats["whisper_total"] = len(wh_all)
    stats["written"] = len(lines)
    return lines, stats


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (America/Denver); default today")
    args = ap.parse_args()
    day = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=TZ)
        if args.date
        else _now_denver()
    )
    date_str = day.strftime("%Y-%m-%d")

    creds = _load_creds()
    OUT_DIR.mkdir(exist_ok=True)
    TMP.mkdir(exist_ok=True)
    out_log = OUT_DIR / f"tea_one_{date_str}.log"
    # Write to a sidecar and swap in only on success. Truncating the real
    # transcript up front meant a mid-run crash (a CUDA OOM at 16:00 on
    # 2026-07-24) destroyed a complete transcript and left a truncated one in
    # its place, with no way back short of re-pulling the audio.
    part_log = OUT_DIR / f"tea_one_{date_str}.log.partial"
    part_log.write_text("")

    client = ProtectApiClient(
        creds["PROTECT_HOST"], 443, creds["PROTECT_LOCAL_USER"], creds["PROTECT_LOCAL_PASS"],
        verify_ssl=False,
    )
    await client.update()
    print(f"[transcribe_day] {date_str}: Protect login OK  (engine={ASR_ENGINE})",
          file=sys.stderr, flush=True)

    if ASR_ENGINE in ("dual", "parakeet"):
        lines, stats = await run_dual(client, day)
        await client.close_session()
        with open(part_log, "a") as f:
            for ln in lines:
                f.write(ln + "\n")
        if not lines and out_log.exists() and out_log.stat().st_size:
            print(f"[transcribe_day] nothing transcribed - keeping existing {out_log}",
                  file=sys.stderr, flush=True)
            part_log.unlink(missing_ok=True)
            sys.stdout.write(out_log.read_text())
            return
        part_log.replace(out_log)
        print(f"[transcribe_day] DONE {date_str}: {len(lines)} lines -> {out_log} "
              f"| parakeet={stats.get('parakeet')} whisper_raw={stats.get('whisper_total')} "
              f"rescued={stats.get('rescued')} suppressed={stats.get('suppressed')}",
              file=sys.stderr, flush=True)
        sys.stdout.write(out_log.read_text())
        return

    model, plan_i = _load_model()

    total = 0
    failed_chunks = 0
    for i, (start, end) in enumerate(_windows(day)):
        mp4 = TMP / f"chunk_{i}.mp4"
        wav = TMP / f"chunk_{i}.wav"
        t0 = time.time()
        try:
            await client.get_camera_video(CAM_ID, start, end, channel_index=CHANNEL, output_file=mp4)
        except Exception as e:
            print(f"[transcribe_day] chunk {i} export failed: {e}", file=sys.stderr, flush=True)
            continue
        if not mp4.exists() or mp4.stat().st_size == 0:
            continue
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
             "-map", "0:a:0", "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
        )
        # faster-whisper yields lazily, so OOM surfaces while draining `segs`,
        # not at the transcribe() call - hence the whole drain is inside the
        # retry. On a VRAM failure, wait for Chloe to let go, then reload one
        # step down the plan; one bad hour must not cost the whole day.
        lines = None
        for attempt in range(3):
            try:
                segs, _ = model.transcribe(
                    str(wav), language="en", vad_filter=True, vad_parameters=VAD_PARAMS,
                    condition_on_previous_text=False, hotwords=HOTWORDS,
                )
                lines = []
                prev, run = None, 0
                for s in segs:
                    text = s.text.strip()
                    if not text or text.lower() in _NOISE:
                        continue
                    if _HALLUCINATION_RE.search(text):
                        print(f"[transcribe_day] dropped silence-hallucination: {text[:60]}",
                              file=sys.stderr, flush=True)
                        continue
                    if text == prev:
                        run += 1
                        if run > MAX_REPEAT:
                            continue
                    else:
                        prev, run = text, 1
                    ts = start + timedelta(seconds=s.start)
                    lines.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S %Z')} | {text}")
                break
            except Exception as e:
                if not _is_oom(e) or attempt == 2:
                    print(f"[transcribe_day] chunk {i} failed: {e}", file=sys.stderr, flush=True)
                    break
                print(f"[transcribe_day] chunk {i} VRAM failure ({_free_vram_mb()}MB free), "
                      f"reloading a step down...", file=sys.stderr, flush=True)
                del model
                time.sleep(20)
                model, plan_i = _load_model(min(plan_i + 1, len(_LOAD_PLAN) - 1))
        if lines is None:
            failed_chunks += 1
            mp4.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
            continue
        with open(part_log, "a") as f:
            for ln in lines:
                f.write(ln + "\n")
        total += len(lines)
        print(
            f"[transcribe_day] {start:%H:%M}-{end:%H:%M} "
            f"{time.time()-t0:.0f}s -> {len(lines)} lines (total {total})",
            file=sys.stderr, flush=True,
        )
        mp4.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)

    await client.close_session()

    # Only now does the previous transcript get replaced. If every chunk failed
    # we keep whatever was already on disk rather than overwriting it with
    # nothing - a stale complete transcript beats a fresh empty one.
    if total == 0 and failed_chunks and out_log.exists() and out_log.stat().st_size:
        print(f"[transcribe_day] {failed_chunks} chunk(s) failed and nothing was "
              f"transcribed - keeping the existing {out_log}", file=sys.stderr, flush=True)
        part_log.unlink(missing_ok=True)
        sys.stdout.write(out_log.read_text())
        return
    part_log.replace(out_log)
    note = f" ({failed_chunks} chunk(s) failed)" if failed_chunks else ""
    print(f"[transcribe_day] DONE {date_str}: {total} lines -> {out_log}{note}",
          file=sys.stderr, flush=True)
    # transcript to stdout for the caller
    sys.stdout.write(out_log.read_text())


if __name__ == "__main__":
    asyncio.run(main())
