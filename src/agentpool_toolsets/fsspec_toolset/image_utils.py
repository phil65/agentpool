"""Image processing utilities for the fsspec toolset."""

from io import BytesIO


# 4.5MB - provides headroom below Anthropic's 5MB limit
DEFAULT_MAX_BYTES = int(4.5 * 1024 * 1024)

# Minimum dimension for resized images
MIN_DIMENSION = 100


def _pick_smaller(
    a: tuple[bytes, str],
    b: tuple[bytes, str],
) -> tuple[bytes, str]:
    """Pick the smaller of two image buffers."""
    return a if len(a[0]) <= len(b[0]) else b


def _make_resize_note(
    original_width: int,
    original_height: int,
    final_width: int,
    final_height: int,
    suffix: str = "",
) -> str:
    """Create a resize note string."""
    scale = original_width / final_width if final_width > 0 else 1.0
    base = f"[Image resized: {original_width}x{original_height} → {final_width}x{final_height}"
    return f"{base}, scale={scale:.2f}x{suffix}]"


def resize_image_if_needed(
    data: bytes,
    media_type: str,
    max_size: int,
    max_bytes: int | None = None,
    jpeg_quality: int = 80,
) -> tuple[bytes, str, str | None]:
    """Resize image if it exceeds limits, preserving aspect ratio.

    Returns the original image if it already fits within all limits.

    Strategy for staying under max_bytes:
    1. First resize to max_size dimensions
    2. Try both PNG and JPEG formats, pick the smaller one
    3. If still too large, try JPEG with decreasing quality
    4. If still too large, progressively reduce dimensions

    Args:
        data: Raw image bytes
        media_type: MIME type of the image
        max_size: Maximum width/height in pixels
        max_bytes: Maximum file size in bytes (default: 4.5MB)
        jpeg_quality: Initial quality for JPEG output (1-100)

    Returns:
        Tuple of (image_data, media_type, dimension_note).
        dimension_note is None if no resizing was needed, otherwise contains
        a message about the resize for the model to map coordinates.
    """
    from PIL import Image

    max_bytes = max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES

    img = Image.open(BytesIO(data))
    original_width, original_height = img.size

    # Check if already within all limits (dimensions AND size)
    original_size = len(data)
    if original_width <= max_size and original_height <= max_size and original_size <= max_bytes:
        return data, media_type, None

    # Calculate initial dimensions respecting max limits
    target_width = original_width
    target_height = original_height

    if target_width > max_size:
        target_height = round(target_height * max_size / target_width)
        target_width = max_size
    if target_height > max_size:
        target_width = round(target_width * max_size / target_height)
        target_height = max_size

    def try_both_formats(
        img: Image.Image,
        width: int,
        height: int,
        quality: int,
    ) -> tuple[bytes, str]:
        """Resize and encode in both formats, returning the smaller one."""
        resized = img.resize((width, height), Image.Resampling.LANCZOS)

        # Convert to RGB for JPEG if needed (handles RGBA, P mode, etc.)
        if resized.mode in ("RGBA", "LA", "P"):
            rgb_img = Image.new("RGB", resized.size, (255, 255, 255))
            if resized.mode == "P":
                resized = resized.convert("RGBA")
            rgb_img.paste(resized, mask=resized.split()[-1] if resized.mode == "RGBA" else None)
            jpeg_source = rgb_img
        else:
            jpeg_source = resized.convert("RGB") if resized.mode != "RGB" else resized

        # Try PNG
        png_buf = BytesIO()
        resized.save(png_buf, format="PNG", optimize=True)
        png_data = png_buf.getvalue()

        # Try JPEG
        jpeg_buf = BytesIO()
        jpeg_source.save(jpeg_buf, format="JPEG", quality=quality, optimize=True)
        jpeg_data = jpeg_buf.getvalue()

        return _pick_smaller((png_data, "image/png"), (jpeg_data, "image/jpeg"))

    # Quality and scale steps for progressive reduction
    quality_steps = [85, 70, 55, 40]
    scale_steps = [1.0, 0.75, 0.5, 0.35, 0.25]

    final_width = target_width
    final_height = target_height

    # First attempt: resize to target dimensions, try both formats
    best_data, best_type = try_both_formats(img, target_width, target_height, jpeg_quality)

    if len(best_data) <= max_bytes:
        note = _make_resize_note(original_width, original_height, final_width, final_height)
        return best_data, best_type, note

    # Still too large - try with decreasing quality
    for quality in quality_steps:
        best_data, best_type = try_both_formats(img, target_width, target_height, quality)

        if len(best_data) <= max_bytes:
            note = _make_resize_note(original_width, original_height, final_width, final_height)
            return best_data, best_type, note

    # Still too large - reduce dimensions progressively
    for scale_factor in scale_steps:
        final_width = round(target_width * scale_factor)
        final_height = round(target_height * scale_factor)

        # Skip if dimensions are too small
        if final_width < MIN_DIMENSION or final_height < MIN_DIMENSION:
            break

        for quality in quality_steps:
            best_data, best_type = try_both_formats(img, final_width, final_height, quality)

            if len(best_data) <= max_bytes:
                note = _make_resize_note(original_width, original_height, final_width, final_height)
                return best_data, best_type, note

    # Last resort: return smallest version we produced even if over limit
    note = _make_resize_note(
        original_width, original_height, final_width, final_height, " (may exceed size limit)"
    )
    return best_data, best_type, note
