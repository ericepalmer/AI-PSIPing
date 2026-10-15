# PSIPing

A local uptime monitor in the style of Pingdom. Add a website, pick how often to check it, and PSIPing records every response.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app
```

Open http://127.0.0.1:8000. The app binds to localhost and stores history in `data/psiping.db`.

## Checks

- Default interval is **60 seconds** (allowed range: 15 seconds to 24 hours).
- Default timeout is **30 seconds**, the usual limit for this kind of synthetic HTTP check.
- A target is **down** when any of these happen:
  - no response before the timeout
  - DNS, connection, or TLS failure
  - more than 10 redirects
  - HTTP status outside 200–399
- Redirects are followed. Only the first 64 KB of the body is read, so a large download does not dominate the timing.
- Screen alerts are on by default and appear when a site goes down or starts responding again. Turn them off per site if you only want the log.

The detail view graphs response time for the selected range, marks failed checks in red, and lists uptime, average and p95 latency, incident count, and the recent response log.
