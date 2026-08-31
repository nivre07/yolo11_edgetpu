"""Model registry: maps short model names to their tflite file on disk.

Picks the Edge-TPU-compiled variant when present, otherwise the plain
full-integer-quant variant (CPU-only). Keeps callers from knowing path layout.

Auto-discovers models from the models/ directory structure.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"


def _build_registry() -> dict[str, tuple[str, str]]:
    """Auto-discover models by scanning the models directory.

    Returns a dict mapping model name to (subdir, file_stem) tuple.
    The file_stem is used to locate _edgetpu.tflite and .tflite variants.
    """
    registry = {}

    if not MODELS_DIR.exists():
        return registry

    # Scan all subdirectories in models/
    for subdir in MODELS_DIR.iterdir():
        if not subdir.is_dir():
            continue

        model_name = subdir.name

        # Find all .tflite files in this subdirectory
        tflite_files = list(subdir.glob("*.tflite"))
        if not tflite_files:
            continue

        # Try to extract stem from tflite files
        # Priority: look for files ending with _edgetpu.tflite first
        stem = None

        # Check for _edgetpu.tflite variant first (this is the compiled variant)
        edgetpu_files = [f for f in tflite_files if "_edgetpu.tflite" in f.name]
        if edgetpu_files:
            # "yolo11L_edgetpu.tflite" -> "yolo11L"
            stem = edgetpu_files[0].name.replace("_edgetpu.tflite", "")
        else:
            # Check for .edgetpu.tflite variant (like "gpt511l.edgetpu.tflite")
            edgetpu_dot_files = [f for f in tflite_files if ".edgetpu.tflite" in f.name]
            if edgetpu_dot_files:
                # "gpt511l.edgetpu.tflite" -> "gpt511l"
                stem = edgetpu_dot_files[0].name.replace(".edgetpu.tflite", "")
            else:
                # Fall back to plain .tflite files (CPU fallback)
                plain_files = [f for f in tflite_files if not any(
                    x in f.name for x in ["_edgetpu", ".edgetpu"]
                )]
                if plain_files:
                    # "yolo11n_best_full_integer_quant.tflite" -> "yolo11n_best_full_integer_quant"
                    stem = plain_files[0].name.replace(".tflite", "")

        if stem:
            registry[model_name] = (model_name, stem)

    return registry


_REGISTRY = _build_registry()
CHOICES = tuple(sorted(_REGISTRY.keys()))
DEFAULT = "Final" if "Final" in CHOICES else (CHOICES[0] if CHOICES else None)


def refresh() -> None:
    """Reload the model registry from disk.

    Call this to pick up new model folders added while the app is running.
    """
    global _REGISTRY, CHOICES, DEFAULT
    _REGISTRY = _build_registry()
    CHOICES = tuple(sorted(_REGISTRY.keys()))
    DEFAULT = "Final" if "Final" in CHOICES else (CHOICES[0] if CHOICES else None)


def resolve(name: str, verbose: bool = True) -> Path:
    """Resolve a model name to its tflite file path.

    Always prefers the Edge-TPU-compiled variant (``*_edgetpu.tflite`` or
    ``*.edgetpu.tflite``) when present. Falls back to the plain integer-quant
    variant.
    """
    if name not in _REGISTRY:
        raise ValueError(f"unknown model {name!r}; choose from {CHOICES}")
    subdir, stem = _REGISTRY[name]
    edgetpu = MODELS_DIR / subdir / f"{stem}_edgetpu.tflite"
    if edgetpu.exists():
        if verbose:
            print(f"[models] {name} -> {edgetpu.name}  (Edge-TPU compiled)", flush=True)
        return edgetpu
    edgetpu_dot = MODELS_DIR / subdir / f"{stem}.edgetpu.tflite"
    if edgetpu_dot.exists():
        if verbose:
            print(f"[models] {name} -> {edgetpu_dot.name}  (Edge-TPU compiled)", flush=True)
        return edgetpu_dot
    cpu = MODELS_DIR / subdir / f"{stem}.tflite"
    if cpu.exists():
        if verbose:
            print(f"[models] {name} -> {cpu.name}  (CPU-only — no Edge-TPU variant)", flush=True)
        return cpu
    raise FileNotFoundError(
        f"no tflite found for {name!r}: tried {edgetpu.name}, {edgetpu_dot.name}, then {stem}.tflite"
    )
