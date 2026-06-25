# Airbnb Chat Capture Tool

An evidence-oriented CLI tool for capturing full-height Airbnb conversation screenshots.

This modular version was extracted from the known-working single-file script. The capture, history-loading, overlay-hiding, stitching, cookie extraction, and worker logic are intended to behave the same as that source script; the code has only been split into files by responsibility.

> Note: This tool and documentation were developed with AI assistance.

## Requirements

- Google Chrome installed
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) recommended

Dependencies are declared in `pyproject.toml`:

- `selenium`
- `webdriver-manager`
- `Pillow`

## Install with uv

```bash
uv sync
```

Run the CLI:

```bash
uv run airbnb-capture --help
```

Compatibility entry point:

```bash
uv run py capture.py --help
```

## First-time login

The tool uses a local Chrome profile folder:

```text
chrome_airbnb_profile/
```

On the first run, or whenever Airbnb asks you to confirm the session, the tool
opens a visible Chrome window before starting the headless workers:

```bash
uv run airbnb-capture 2569717633
```

Log in or click Airbnb's continue/confirmation button in that Chrome window.
The tool waits for the conversation to become accessible, then extracts cookies
from the saved profile and starts the capture automatically.

You can still use diagnose mode when you want to inspect selectors or refresh
login without running a capture:

```bash
uv run airbnb-capture 2569717633 --diagnose
```

Do not commit `chrome_airbnb_profile/`.

## Usage

Capture one conversation:

```bash
uv run airbnb-capture 2569717633
```

Capture one conversation to a specific file:

```bash
uv run airbnb-capture 2569717633 --out evidence/chat_record.jpg
```

Capture multiple conversations:

```bash
uv run airbnb-capture 2569717633 2507140193 2534821074 --out-dir evidence/
```

Capture from a file:

```bash
uv run airbnb-capture --ids-file conversations.txt
```

`conversations.txt` should contain one conversation ID per line. Blank lines and lines beginning with `#` are ignored.

## CLI Options

| Option | Description |
| --- | --- |
| `conversation_ids` | One or more Airbnb conversation IDs. |
| `--ids-file FILE` | Read conversation IDs from a text file. |
| `--out PATH` | Output path for a single capture. |
| `--out-dir DIR` | Output directory for bulk capture. Default: `screenshots`. |
| `--domain URL` | Airbnb domain. Default: `https://www.airbnb.co.uk`. |
| `--diagnose` | Print DOM information, save a diagnostic screenshot, and wait before closing Chrome. |
| `--no-details` | Capture only the chat panel. |
| `--delay SECONDS` | Extra wait after page load. Default: `3.0`. |
| `--workers N` | Maximum parallel Chrome workers. Default: `4`. |
| `--verbose` | Enable debug logging. |

## Project Structure

```text
capture.py
pyproject.toml
requirements.txt
README.md

airbnb_capture/
  cli.py
  config.py
  models.py
  tmp.py
  network.py
  paths.py

  browser/
    factory.py
    cookies.py

  dom/
    js.py
    selectors.py
    overlays.py
    diagnostics.py

  capture/
    conversation.py
    history.py
    stitcher.py
    panels.py

  output/
    banner.py
    writer.py

  orchestration/
    bulk.py
    sequential.py
```

## Troubleshooting

### Redirects to login

Run the capture again and complete Airbnb's login or continue prompt in the
visible Chrome window. To inspect the page without running a capture, use
diagnose mode:

```bash
uv run airbnb-capture 2569717633 --diagnose
```

### Chrome renderer startup errors

Reduce worker count:

```bash
uv run airbnb-capture --ids-file conversations.txt --workers 2
```

### History appears incomplete or banner appears again

This package should preserve the exact working history and overlay logic from the source script. Run the original single-file script and this modular version with `--verbose` on the same conversation and compare the logged `nodes`, `scrollHeight`, `text`, and strip count.

## Development

Add dependencies:

```bash
uv add package-name
```

Remove dependencies:

```bash
uv remove package-name
```

Commit `uv.lock` for reproducible local runs.
