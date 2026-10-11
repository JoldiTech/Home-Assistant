# Hand-written comparison logs

These four files are the control arm of
[`../EVALUATION-4-day-blind-rewrite.md`](../EVALUATION-4-day-blind-rewrite.md).

Each was written from the pipeline's *exact* summarizer input for that day —
the POS-woven, hour-compacted transcript plus the records index, context block
and Slack, dumped straight out of `captains_pipeline` without running the
summarizer — under the same `SYSTEM_PROMPT` policy and the same output format.

They are here so the comparison can be checked rather than taken on trust: the
published version of each day is on the `captains-log` branch at
`captains_log/<date>.md`. The deterministic "By the numbers" block is identical
in both versions and is omitted here.

They are **not** replacements for the published logs and should not be treated
as the record of those days. The published day-files remain the record.

Reproduce the input dump with:

```python
import captains_pipeline as P
from datetime import datetime, timedelta
day  = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=P.TZ)
env  = P._load_env()
end  = day.replace(hour=18, minute=0, second=0, microsecond=0)
biz  = P._fetch_business(env, date_str)
slack_text, slack_names = P._fetch_slack(env, end - timedelta(days=1), end)
transcript = (P.Path.home() / "captains_transcripts" / f"tea_one_{date_str}.log").read_text()
compact = P._compact_transcript(P._weave_orders(transcript, biz.get("sales"), date_str))
```

Run it on the AI box with the CUDA libs on the path, or `llama_cpp` fails to
import before any of this executes:

```bash
export LD_LIBRARY_PATH=$(ls -d ~/transcribe-env/lib/python3.12/site-packages/nvidia/*/lib | tr '\n' ':')
```
