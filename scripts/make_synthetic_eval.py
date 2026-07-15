"""Generate a synthetic photo-damage eval set from cached reference images.

Takes clean card renders from ``data/images/`` and produces distorted copies —
perspective jitter, brightness/contrast shift, a glare blob, blur, and JPEG
re-compression — named ``{card_id}__distorted.jpg`` so ``pokeum eval`` can score
them. Deterministic for a given seed, and implemented with PIL only (independent
of the training harness's torch augmentations, so it is not the model grading
its own homework).

Usage::

    python scripts/make_synthetic_eval.py --out data/eval/synthetic --count 20
"""

from __future__ import annotations

import argparse
import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def _perspective_coeffs(
    src: list[tuple[float, float]], dst: list[tuple[float, float]]
) -> list[float]:
    """Return PIL PERSPECTIVE transform coefficients mapping ``src`` to ``dst``."""
    matrix = []
    vector = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vector += [u, v]
    solution = np.linalg.solve(np.array(matrix, dtype=float), np.array(vector, dtype=float))
    return [float(c) for c in solution]


def distort(img: Image.Image, rng: random.Random) -> Image.Image:
    """Apply photo-like damage to one clean card render.

    Args:
        img: The clean RGB card image.
        rng: Seeded generator driving every random choice.

    Returns:
        The distorted image (post JPEG round-trip).
    """
    width, height = img.size
    jitter = 0.05
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    moved = [
        (x + rng.uniform(-jitter, jitter) * width, y + rng.uniform(-jitter, jitter) * height)
        for x, y in corners
    ]
    img = img.transform(
        (width, height), Image.PERSPECTIVE, _perspective_coeffs(moved, corners), Image.BICUBIC
    )
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.6, 1.25))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.75, 1.2))

    glare = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(glare)
    cx = rng.uniform(0.2, 0.8) * width
    cy = rng.uniform(0.2, 0.8) * height
    radius = rng.uniform(0.15, 0.4) * width
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=140)
    glare = glare.filter(ImageFilter.GaussianBlur(radius / 2))
    img = Image.composite(Image.new("RGB", (width, height), (255, 255, 255)), img, glare)

    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.8)))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=rng.randint(45, 75))
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def main() -> None:
    """Parse arguments and write the distorted eval set."""
    parser = argparse.ArgumentParser(description="Generate a synthetic eval set")
    parser.add_argument("--images", default="data/images", help="cached reference images root")
    parser.add_argument("--out", default="data/eval/synthetic", help="output directory")
    parser.add_argument("--count", type=int, default=20, help="number of cards to distort")
    parser.add_argument("--seed", type=int, default=7, help="random seed")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    root = Path(args.images)
    paths = sorted(p for p in root.glob("*/*") if p.suffix in {".png", ".webp", ".jpg"})
    rng.shuffle(paths)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in paths[: args.count]:
        image = Image.open(path).convert("RGB")
        distort(image, rng).save(out_dir / f"{path.stem}__distorted.jpg", quality=90)
    print(f"wrote {min(args.count, len(paths))} distorted images to {out_dir}")


if __name__ == "__main__":
    main()
