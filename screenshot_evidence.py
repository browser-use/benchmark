"""Prepare chronological screenshot evidence without changing task scores."""

import base64
import binascii
from io import BytesIO
from math import sqrt

from PIL import Image, UnidentifiedImageError


def _sample_indices(images: list[str], maximum: int) -> list[int]:
    if not images or maximum <= 0:
        return []
    # Collapse adjacent repeats only. Keep the actual first and final captures.
    indices = [0]
    for index in range(1, len(images)):
        if images[index] != images[index - 1]:
            indices.append(index)
    if indices[-1] != len(images) - 1:
        if len(indices) > 1:
            indices[-1] = len(images) - 1
        else:
            indices.append(len(images) - 1)
    if maximum == 1:
        return [indices[-1]]
    count = min(maximum, len(indices))
    return (
        [indices[round(i * (len(indices) - 1) / (count - 1))] for i in range(count)]
        if count > 1
        else indices
    )


def _png(image: Image.Image) -> str:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _jpeg(image: Image.Image, quality: int) -> str:
    rgba = image.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, "white")
    canvas.paste(rgba, mask=rgba.getchannel("A"))
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=quality, subsampling=0)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _fit(
    encoded: str, budget: int
) -> tuple[str | None, tuple[int, int], tuple[int, int], str]:
    with Image.open(BytesIO(base64.b64decode(encoded, validate=True))) as original:
        original.load()
        size = original.size
        if original.format in {"PNG", "JPEG"} and len(encoded) <= budget:
            return encoded, size, size, Image.MIME[original.format]
        image = original.convert("RGBA")
        packed = _png(image)
        if len(packed) <= budget:
            return packed, size, size, "image/png"
        transparent = image.getextrema()[3][0] < 255
        # Compress before reducing resolution, retaining UI text where possible.
        for quality in () if transparent else (90, 80):
            packed = _jpeg(image, quality)
            if len(packed) <= budget:
                return packed, size, size, "image/jpeg"
        while len(packed) > budget:
            if image.size == (1, 1):
                tiny_png = _png(image)
                return (
                    (tiny_png if len(tiny_png) <= budget else None),
                    size,
                    image.size,
                    "image/png",
                )
            ratio = min(0.85, sqrt(max(budget, 1) / len(packed)) * 0.9)
            dimensions = tuple(max(1, int(value * ratio)) for value in image.size)
            image = image.resize(dimensions, Image.Resampling.LANCZOS)
            packed = _png(image) if transparent else _jpeg(image, 85)
        return packed, size, image.size, "image/png" if transparent else "image/jpeg"


def _allocate(sizes: list[int], budget: int) -> list[int]:
    """Leave small images unchanged and share remaining bytes among larger ones."""
    allocations = [0] * len(sizes)
    pending = list(range(len(sizes)))
    remaining = max(0, budget)
    while pending:
        share = remaining // len(pending)
        small = [index for index in pending if sizes[index] <= share]
        if not small:
            for offset, index in enumerate(pending):
                allocations[index] = share + (offset < remaining % len(pending))
            break
        for index in small:
            allocations[index] = sizes[index]
            remaining -= sizes[index]
        pending = [index for index in pending if index not in small]
    return allocations


def prepare_screenshots(
    images: list[str], max_images: int, max_bytes: int
) -> tuple[list[str], dict]:
    indices = _sample_indices(images, max_images)
    sizes = [
        len(images[index]) if isinstance(images[index], str) else 0 for index in indices
    ]
    budgets = _allocate(sizes, max_bytes)
    prepared = []
    metadata = {
        "input_count": len(images),
        "source_indices": [],
        "resized": [],
        "reencoded": [],
        "mime_types": [],
        "omitted": [],
        "notes": [],
    }
    for index, budget in zip(indices, budgets):
        try:
            packed, before, after, mime_type = _fit(images[index], budget)
        except (
            ValueError,
            TypeError,
            OSError,
            SyntaxError,
            binascii.Error,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            metadata["omitted"].append(
                {"source_index": index, "reason": "unreadable image"}
            )
            metadata["notes"].append(
                f"Source screenshot {index + 1} was unreadable and is not attached."
            )
            continue
        if packed is None:
            metadata["omitted"].append(
                {"source_index": index, "reason": "image cannot fit byte budget"}
            )
            metadata["notes"].append(
                f"Source screenshot {index + 1} could not fit the image budget even after resizing and is not attached."
            )
            continue
        prepared.append(packed)
        metadata["mime_types"].append(mime_type)
        if packed != images[index]:
            metadata["reencoded"].append(index)
            if before == after:
                metadata["notes"].append(
                    f"Attached screenshot {len(prepared)} was compressed as {mime_type} without changing its dimensions."
                )
        metadata["source_indices"].append(index)
        if before != after:
            metadata["resized"].append(
                {"source_index": index, "before": before, "after": after}
            )
            metadata["notes"].append(
                f"Attached screenshot {len(prepared)} was resized from {before[0]}x{before[1]} to {after[0]}x{after[1]}; small text may be less legible."
            )
    metadata["selected_count"] = len(prepared)
    metadata["encoded_bytes"] = sum(len(image) for image in prepared)
    metadata["notes"].insert(
        0,
        f"Attached {len(prepared)} of {len(images)} source screenshots. Source positions (not action step numbers): {[index + 1 for index in metadata['source_indices']]}. Repeated states at different times are retained when sampled.",
    )
    return prepared, metadata
