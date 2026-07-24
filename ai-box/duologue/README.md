# Duologue — multi-persona chat on the AI box

A small, self-contained orchestrator that lets **two (or more) named personas**
share **one local llama.cpp model** and hold a conversation with you *and each
other*. You name each persona and write their personality in a settings page;
every turn, each persona replies round-robin, and each one sees the others'
replies in the same turn. Address someone by name ("Ada, what do you think?")
and that persona knows it was spoken to and answers first.

It runs entirely on the AI box (`nmteacoaiserver`) and **does not touch Chloe**
(`imagegen/app.py`) — it's its own process on its own port.

## Design: how the personas stay separate with one model

A chat LLM is stateless between calls — identity and memory live only in the
messages you pass. Duologue keeps a single neutral, **speaker-labeled
transcript** and *renders it per persona at call time*:

- each persona gets its own strong **system prompt** (name + personality + "only
  ever speak as yourself; never write another participant's lines");
- the shared transcript is fed as one labeled block, then "now write **Name**'s
  reply";
- **stop sequences** on the other speakers' name tags prevent it from writing
  anyone else's turn;
- calls are **serialized** (a single llama.cpp context isn't safe to decode in
  parallel — and turn-taking wants serialization anyway).

So one model instance is enough, and the personas never cross-talk unless the
orchestrator allows it. Loading a second copy of the weights would buy no extra
separation — you'd only load a *different* model if you wanted a genuinely
different "voice."

## Model-agnostic

Nothing is hard-coded to a model. The settings page lists every `.gguf` under
`~/transcribe/models` and `~/imagegen/models`; pick one at startup. Each model's
**own chat template** (from its GGUF metadata) is used, so Qwen / Gemma /
Mistral-Nemo / etc. all work. The model loads on the first message and reloads
if you change the selection.

## Run it

Uses the box's existing CPU `llama_cpp` (in `~/imagegen-env`) — no install:

```bash
cd ~/duologue
CUDA_VISIBLE_DEVICES="" ~/imagegen-env/bin/python duologue.py
# -> duologue listening on http://127.0.0.1:8477
```

It binds to **localhost only**. From your laptop, reach it over an SSH tunnel:

```bash
ssh -L 8477:127.0.0.1:8477 aibox    # then open http://localhost:8477
```

State (personas + transcript) persists to `~/duologue/state.json`.

### Environment knobs

| Var | Default | Meaning |
|---|---|---|
| `DUOLOGUE_HOST` | `127.0.0.1` | bind address (keep localhost unless deliberately exposing) |
| `DUOLOGUE_PORT` | `8477` | port |
| `DUOLOGUE_DATA` | `~/duologue` | state directory |

## Run as a service (optional)

`duologue.service` is a ready systemd unit (localhost-bound, CPU-only). Install
when you want it always-on:

```bash
mkdir -p ~/.config/systemd/user
cp duologue.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now duologue
```

## Footprint / notes

- Chat runs on **CPU** (leaves the GPU for Chloe's image gen). A 7B model is
  ~4–5 GB RAM and answers in a few seconds; 12B models are heavier/slower. A
  round with two personas = two sequential generations.
- Public exposure (a Cloudflare hostname like Chloe's) is intentionally **not**
  set up — this is localhost + SSH tunnel until you decide otherwise.
