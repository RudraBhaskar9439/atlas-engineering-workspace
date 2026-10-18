import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
DATA = Path(os.getenv("ATLAS_DATA_DIR", ROOT / ".atlas-data"))
DATA.mkdir(parents=True, exist_ok=True)
BASE_URL = os.getenv("ATLAS_BASE_URL", "http://localhost:8001").rstrip("/")
REDIRECT_URI = BASE_URL + "/auth/google/callback"
DB_HOST = os.getenv("FALKORDB_HOST", "localhost")
DB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
DB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
# Approved development cap. The app reserves a conservative amount before every paid operation.
API_CAP_INR = float(os.getenv("ATLAS_API_CAP_INR", "500"))
DEMO_ENABLED = os.getenv("ATLAS_DEMO_ENABLED", "true").lower() == "true"
if urlparse(BASE_URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
    if DEMO_ENABLED or not os.getenv("ATLAS_ALLOWED_EMAILS") or not BASE_URL.startswith("https://"):
        raise RuntimeError(
            "Public hosting requires HTTPS, ATLAS_DEMO_ENABLED=false, and ATLAS_ALLOWED_EMAILS."
        )


def secret_file(name: str, generator) -> str:
    path = DATA / name
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                file.write(generator())
        except FileExistsError:
            pass
    return path.read_text().strip()
