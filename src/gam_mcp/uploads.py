"""Safe local file access for creative uploads.

Upload tools only read files inside a dedicated directory configured with
GAM_UPLOAD_DIR. Paths are resolved (following symlinks) and must stay inside
that directory; only regular files up to MAX_UPLOAD_BYTES are read, and their
contents must decode as a PNG, JPEG or GIF image.
"""

import os
import stat
import struct
from pathlib import Path
from typing import Optional

UPLOAD_DIR_ENV = "GAM_UPLOAD_DIR"

# Ad Manager rejects image creatives larger than this anyway
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif"}


class UploadError(ValueError):
    """Raised when an upload path or file is not acceptable."""


def get_upload_root() -> Path:
    """Return the resolved upload root directory.

    Raises:
        UploadError: If GAM_UPLOAD_DIR is unset or not an existing directory
    """
    root = os.environ.get(UPLOAD_DIR_ENV, "").strip()
    if not root:
        raise UploadError(
            f"{UPLOAD_DIR_ENV} is not set. Set it to a dedicated directory that holds "
            "creative files; uploads are only read from inside that directory."
        )
    try:
        resolved = Path(root).resolve(strict=True)
    except (OSError, RuntimeError):
        raise UploadError(f"{UPLOAD_DIR_ENV} does not exist: {root}")
    if not resolved.is_dir():
        raise UploadError(f"{UPLOAD_DIR_ENV} is not a directory: {root}")
    return resolved


def resolve_upload_path(path_str: str, expect_dir: bool = False) -> Path:
    """Resolve a caller-supplied path inside the upload root.

    Relative paths are taken relative to the upload root. Symlinks are
    followed, and the final target must still be inside the upload root.

    Args:
        path_str: Path supplied by the caller
        expect_dir: True to require a directory, False to require a regular file

    Returns:
        The resolved path

    Raises:
        UploadError: If the path is missing, outside the root, or the wrong type
    """
    root = get_upload_root()
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise UploadError(f"Path not found: {path_str}")

    if resolved != root and root not in resolved.parents:
        raise UploadError(f"Path is outside the upload directory ({UPLOAD_DIR_ENV}): {path_str}")

    if expect_dir:
        if not resolved.is_dir():
            raise UploadError(f"Not a directory: {path_str}")
    elif not stat.S_ISREG(resolved.stat().st_mode):
        raise UploadError(f"Not a regular file: {path_str}")

    return resolved


def read_upload_file(path: Path) -> bytes:
    """Read a regular file from an already resolved upload path, enforcing the size limit."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        raise UploadError(f"Cannot open {path.name}: {e.strerror}")

    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise UploadError(f"Not a regular file: {path.name}")
        if st.st_size > MAX_UPLOAD_BYTES:
            raise UploadError(
                f"{path.name} is {st.st_size} bytes; the limit is {MAX_UPLOAD_BYTES} bytes"
            )
        data = f.read(MAX_UPLOAD_BYTES + 1)

    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"{path.name} exceeds the {MAX_UPLOAD_BYTES} byte limit")
    return data


def _jpeg_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    """Find the frame size in a JPEG's SOF segment."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length field
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def image_dimensions(data: bytes) -> tuple[str, int, int]:
    """Validate image bytes and return (format, width, height).

    Raises:
        UploadError: If the data is not a PNG, JPEG or GIF with readable dimensions
    """
    dims: Optional[tuple[int, int]] = None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        fmt = "png"
        dims = struct.unpack(">II", data[16:24])
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        fmt = "gif"
        dims = struct.unpack("<HH", data[6:10])
    elif data.startswith(b"\xff\xd8\xff"):
        fmt = "jpeg"
        dims = _jpeg_dimensions(data)
    else:
        raise UploadError("File is not a PNG, JPEG or GIF image")

    if not dims or dims[0] <= 0 or dims[1] <= 0:
        raise UploadError(f"Could not read image dimensions from {fmt.upper()} data")
    return fmt, dims[0], dims[1]
