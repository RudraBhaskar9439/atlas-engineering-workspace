"""Package reproducible source, documentation and verification artifacts without local secrets."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "Dockerfile",
    "compose.yaml",
    "pyproject.toml",
    "uv.lock",
    "codegraph-requirements.txt",
    "README.md",
    "ARCHITECTURE.md",
    "THIRD_PARTY.md",
    "DEMO.md",
    "SUBMISSION.md",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
]
DIRECTORIES = ["atlas", "demo", "frontend/src", "frontend/public", "frontend/tests", "scripts", "tests", "outputs"]
EXTENSIONS = {".py", ".sh", ".ts", ".tsx", ".js", ".css", ".md", ".json", ".txt", ".jpg", ".png", ".svg"}
EXCLUDED = {".env", ".git", ".atlas-data", ".tools", ".venv", "node_modules", "__pycache__", "dist"}


def package():
    paths = {ROOT / name for name in FILES}
    paths.update(
        p
        for name in DIRECTORIES
        for p in (ROOT / name).rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and p.suffix in EXTENSIONS
        and not EXCLUDED.intersection(p.relative_to(ROOT).parts)
    )
    destination = ROOT / "outputs" / "atlas-source.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(destination) as archive:
        names = archive.namelist()
        assert ".env" not in names and all(not EXCLUDED.intersection(Path(n).parts) for n in names)
        assert {"README.md", "atlas/main.py", "frontend/src/App.tsx", "uv.lock"}.issubset(names)
        assert archive.testzip() is None
    print(f"Source package verified: {len(names)} files, {destination.stat().st_size:,} bytes")
    return destination


if __name__ == "__main__":
    package()
