#!/usr/bin/env python3
"""GPU chat worker - loads a GGUF model (optionally offloaded to the GPU) and
streams chat completions over stdin/stdout as JSON lines.

Run under an environment whose llama-cpp-python has the CUDA build when GPU
offload is wanted (on the AI box that is transcribe-env). Kept a SEPARATE
process on purpose: it lets the "GPU chat" mode use the CUDA build while the
main imagegen process keeps the CPU-only build for the "chat + images" mode,
so the two never fight over one llama build - and a chat crash can't take the
image service down.

Protocol (one JSON object per line, both directions):
  stdout, once after load:  {"ready": true, "gpu_layers": N}
                            or {"error": "..."} then {"fatal": true} on failure
  stdin, a request:         {"messages":[...], "max_tokens":N, "temperature":T,
                             "top_p":P, "repeat_penalty":R}
  stdout, per request:      {"delta":"..."} lines, terminated by {"done": true}
                            ({"error":"..."} before {"done"} on generation error)
  stdin, mid-generation:    {"cancel": true}  -> stop this turn early, emit {"done"}

The worker polls stdin between tokens (non-blocking select) so a cancel lands
within one token, without a second thread.
"""
import json
import select
import sys

from llama_cpp import Llama


def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    model_path = sys.argv[1]
    target_layers = int(sys.argv[2])
    n_ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 4096

    # Fallback chain: a 12B Q4 doesn't fully fit a 6GB card, so try the target
    # offload then progressively less (descending), so a tight card still gets
    # SOME GPU speed instead of failing outright.
    if target_layers > 0:
        raw_chain = [target_layers, target_layers * 3 // 4, target_layers // 2, 8]
    else:
        raw_chain = [0]
    seen = set()
    chain = [n for n in raw_chain if n > 0 and not (n in seen or seen.add(n))] or [target_layers]
    llm, used = None, 0
    for layers in chain:
        try:
            llm = Llama(model_path=model_path, n_ctx=n_ctx, n_threads=8,
                        n_gpu_layers=layers, verbose=False)
            used = layers
            break
        except Exception as e:
            print(f"worker: load with {layers} layers failed: {e}", file=sys.stderr, flush=True)
    if llm is None:
        _emit({"error": "model failed to load on GPU and CPU"})
        _emit({"fatal": True})
        return
    _emit({"ready": True, "gpu_layers": used})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("cancel"):  # stray cancel outside a turn - ignore
            continue
        try:
            stream = llm.create_chat_completion(
                messages=req.get("messages", []),
                max_tokens=req.get("max_tokens", 512),
                stream=True,
                temperature=req.get("temperature", 0.75),
                top_p=req.get("top_p", 0.9),
                repeat_penalty=req.get("repeat_penalty", 1.18),
            )
            for chunk in stream:
                # Non-blocking check for a cancel line arriving on stdin.
                r, _, _ = select.select([sys.stdin], [], [], 0)
                if r:
                    ctl = sys.stdin.readline()
                    if ctl and json.loads(ctl or "{}").get("cancel"):
                        break
                delta = (chunk["choices"][0].get("delta") or {}).get("content")
                if delta:
                    _emit({"delta": delta})
        except Exception as e:
            _emit({"error": str(e)[:200]})
        _emit({"done": True})


if __name__ == "__main__":
    main()
