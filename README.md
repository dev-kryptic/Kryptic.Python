# kryptic-daemon

The Kryptic daemon client for Python. During development startup it asks the local
Kryptic daemon for the current project's secrets and puts them into `os.environ`.
Outside development it is a no-op. It never raises - no daemon just means your app
starts with the environment it already has.

```bash
pip install kryptic-daemon
```

```python
import kryptic
kryptic.inject()  # call before any os.environ reads

import os
db_url = os.environ["DATABASE_URL"]  # now populated
```

Django (`manage.py`, before `django.setup()`):

```python
import kryptic
kryptic.inject()
```

Works with Django, FastAPI, Flask, and anything else that reads `os.environ`.

## Behavior

- No-op when `ENVIRONMENT`/`ENV`/`PYTHON_ENV`/`APP_ENV` is production/staging,
  or `KRYPTIC_DISABLED=true`.
- Finds `kryptic.json` by walking up from the working directory.
- Never overwrites environment variables that are already set.
- Configuration via env vars: `KRYPTIC_PROJECT_ID`, `KRYPTIC_ENV`, `KRYPTIC_SOCKET_PATH`,
  `KRYPTIC_TIMEOUT_MS` (default 2000), `KRYPTIC_SILENT`.
- Works on macOS/Linux (unix sockets) and Windows (named pipes).

Protocol: see [daemon/PROTOCOL.md](https://github.com/dev-kryptic/Kryptic.Daemon/blob/main/PROTOCOL.md). License: Apache-2.0.

```bash
python3 -m unittest discover -s tests
```
