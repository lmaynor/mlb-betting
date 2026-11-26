"""Image-packaging guard for the Kalshi RFQ recorder.

The recorder's Cloud Run image is assembled by the root Dockerfile, which COPYs
individual files out of scripts/ (the rest of scripts/ is local/ops tooling and
is deliberately NOT in the image). Any `scripts.<module>` import in a file that
IS in the image must therefore have a matching COPY line, or the import fails
in-image with ModuleNotFoundError while passing on a full repo checkout.

That is exactly what happened to scripts.rfq_tape_finalize on the 2026-11-24
recorder verification run: the daemon imports it at record-mode startup, the
Dockerfile never copied it, and the failure was warning-only (the derived
Parquet layer silently never built). This test makes that class of defect a CI
failure instead of a log line.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files the recorder image runs or may import at runtime. Extend this list when
# the recorder gains another in-image entry point.
IN_IMAGE_ENTRY_POINTS = (
    "scripts/kalshi_rfq_daemon.py",
    "scripts/rfq_tape_finalize.py",
)

_SCRIPTS_IMPORT = re.compile(r"(?:from|import)\s+scripts\.([A-Za-z_][A-Za-z0-9_]*)")
_DOCKERFILE_COPY = re.compile(r"^COPY\s+scripts/([A-Za-z0-9_.\-/]+)\s", re.MULTILINE)


def _dockerfile_copied_scripts() -> set[str]:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    copied = set()
    for src in _DOCKERFILE_COPY.findall(dockerfile):
        # COPY scripts/<file>.py or COPY scripts/<dir>/ -- record the top module name.
        copied.add(src.split("/")[0].removesuffix(".py"))
    return copied


def _scripts_modules_imported_by(rel_path: str) -> set[str]:
    return set(_SCRIPTS_IMPORT.findall((REPO_ROOT / rel_path).read_text()))


def test_every_scripts_import_of_in_image_files_is_copied():
    copied = _dockerfile_copied_scripts()
    missing = {}
    for entry in IN_IMAGE_ENTRY_POINTS:
        for mod in _scripts_modules_imported_by(entry):
            if mod not in copied:
                missing.setdefault(entry, []).append(mod)
    assert not missing, (
        f"in-image files import scripts.* modules the Dockerfile does not COPY: {missing}"
    )


def test_rfq_tape_finalize_is_in_the_image():
    # The 2026-11-24 regression, named explicitly so the failure message is obvious.
    assert "rfq_tape_finalize" in _dockerfile_copied_scripts()
    assert "rfq_tape_finalize" in _scripts_modules_imported_by("scripts/kalshi_rfq_daemon.py")
