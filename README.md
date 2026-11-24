# Trace — personal digital activity record

Trace is a single-user web app for recording daily Instagram and YouTube hours, AI prompts, and Google searches. It saves entries by date in SQLite and estimates emissions for each day and month.

## Run locally

Requires Python 3.9 or newer. No packages need to be installed.

1. Open a terminal in this folder.
2. Run `python app.py`.
3. Open <http://127.0.0.1:8765>.

The SQLite file `trace_records.sqlite3` is created on first launch and stays on the local computer. It is excluded from Git. Back up that file separately to back up the personal history. Saving the same date again updates that entry.

## Deploy from GitHub with Railway

GitHub Pages only serves static files, so it cannot run this Python API or retain SQLite records. This project is configured for Railway to deploy from a GitHub repository and run the Python backend. For persistent records, attach a Railway Volume mounted at `/data`.

Set these Railway variables:

- `TRACE_USERNAME`: the login name for the personal tracker.
- `TRACE_PASSWORD`: a long, unique password. Keep it in Railway's secret variables, never in GitHub.
- `TRACE_DB_PATH`: `/data/trace_records.sqlite3` (or rely on Railway's `RAILWAY_VOLUME_MOUNT_PATH`).

Railway supplies `PORT`; `railway.toml` uses `/health` for its health check. The app refuses to bind publicly without both login variables. Railway terminates HTTPS at the public endpoint, protecting the HTTP Basic login in transit. Keep the repository private because the source project is personal; the SQLite database, logs, and `.env` files are ignored by Git.

`Dockerfile` and `railway.toml` provide the runtime configuration. The Railway service still needs to be connected to the GitHub repository, have the persistent volume mounted at `/data`, and receive the two secret login variables before it can be safely published.

## Data model

Each row stores one date, Instagram hours, YouTube hours, AI prompts, Google searches, grid carbon intensity, activity-level estimates, total estimate, estimate version, and timestamps. The server validates values and uses a SQLite upsert so each date has one editable row.

## Estimate method

- Instagram and YouTube: 55 g CO₂e/hour, a broad streaming proxy including viewing device and network estimates.
- AI: 0.24 Wh per text prompt × the saved grid intensity.
- Google Search: 0.30 Wh per query × the saved grid intensity.

The AI energy value is based on a provider-published median text-prompt benchmark. The search value is an older public estimate. The streaming value is an average European estimate, not a phone- or app-specific measurement. Actual emissions vary by model, prompt, network, device, region and energy supply. The app excludes phone manufacturing and phone-specific power measurement.

Sources: [Google AI inference methodology](https://cloud.google.com/blog/products/infrastructure/measuring-the-environmental-impact-of-ai-inference), [Google Search estimate (2009)](https://googleblog.blogspot.com/2009/01/powering-google-search.html), [Carbon Trust streaming estimate](https://www.carbontrust.com/our-work-and-impact/guides-reports-and-tools/carbon-impact-of-video-streaming).
