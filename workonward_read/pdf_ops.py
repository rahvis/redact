"""
Lossless PDF tools layer for WorkOnward Read.

This module implements PDF manipulation that never rasterizes page content:
merging, splitting, page extraction, encryption/decryption, sanitizing,
document properties, page rotation, and the PageOpsJournal used to record
page-organization edits (delete/move/rotate/insert/crop) so they can be
replayed both on the in-memory page images and — losslessly via pypdf — on
the original PDF file.

Business-layer module: it must never import FreeSimpleGUI or tkinter and it
never contains user-visible (translatable) strings. Errors are raised as
exceptions with plain-English messages; callers in the GUI layer translate.

Coordinates follow the application-wide convention: pixel values refer to
ORIGINAL-image pixel space at 200 PPI, y-down. ``pt = px * 72/200`` and the
PDF y-axis is flipped (``y_pt = page_height_pt - y_px * 72/200``).

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
from contextlib import ExitStack, contextmanager
from datetime import datetime

from PIL import Image
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions
from pypdf.generic import RectangleObject

# Application-wide import resolution (see docs/dev-architecture.md).
IMPORT_PPI = 200
PT_PER_PX = 72.0 / IMPORT_PPI
PX_PER_PT = IMPORT_PPI / 72.0

# PIL transpose constants for CLOCKWISE rotation by degrees.
# (PIL's ROTATE_* constants rotate counterclockwise.)
_CW_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

_VALID_OPS = ("delete", "move", "rotate", "insert_blank", "insert_pdf", "crop")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decrypt(reader, path, password):
    """Decrypt `reader` in place if encrypted. Raise ValueError on failure."""
    if not reader.is_encrypted:
        return
    try:
        result = reader.decrypt(password if password is not None else "")
    except Exception as exc:  # malformed encryption dictionaries etc.
        raise ValueError(f"Could not decrypt '{path}': {exc}") from exc
    if not result:
        if password is None:
            raise ValueError(f"'{path}' is encrypted and requires a password.")
        raise ValueError(f"Incorrect password for '{path}'.")


@contextmanager
def _open_reader(path, password=None):
    """Yield a decrypted PdfReader for `path`, closing the file afterwards."""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "rb") as fh:
        reader = PdfReader(fh)
        _decrypt(reader, path, password)
        yield reader


def _check_page_index(idx, page_count, path):
    if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < page_count:
        raise ValueError(
            f"Page index {idx!r} out of range for '{path}' ({page_count} pages)."
        )


def _replace_container_image(container, new_image):
    """Swap `container.image` for `new_image`, closing the old resources.

    Works by duck-typing so any object with `image` (and optionally
    `scaled_image`, `scale_image()`) attributes is supported.
    """
    old = getattr(container, "image", None)
    scaled = getattr(container, "scaled_image", None)
    container.image = new_image
    if hasattr(container, "scaled_image"):
        container.scaled_image = new_image
    if scaled is not None and scaled is not old and scaled is not new_image:
        try:
            scaled.close()
        except Exception:
            pass
    if old is not None and old is not new_image:
        try:
            old.close()
        except Exception:
            pass
    # Re-derive the display image at the current zoom if the container can.
    rescale = getattr(container, "scale_image", None)
    if callable(rescale):
        try:
            rescale()
        except Exception:
            pass


def _set_container_size(container, width_pt, height_pt):
    container.width_in_pt = width_pt
    container.height_in_pt = height_pt
    if hasattr(container, "size"):
        # ImageContainer stores size as (height_pt, width_pt).
        container.size = (height_pt, width_pt)


# ---------------------------------------------------------------------------
# Merge / split / extract
# ---------------------------------------------------------------------------

def merge_pdfs(inputs, output, passwords=None):
    """Merge `inputs` (list of paths) into `output`. Return total page count.

    Args:
        inputs: Iterable of PDF file paths, merged in order.
        output: Destination path.
        passwords: Optional dict mapping an input path to its password.

    Returns:
        int: Number of pages in the merged document.
    """
    inputs = list(inputs)
    if not inputs:
        raise ValueError("At least one input PDF is required for merging.")
    passwords = passwords or {}
    writer = PdfWriter()
    total = 0
    with ExitStack() as stack:
        for path in inputs:
            reader = stack.enter_context(_open_reader(path, passwords.get(path)))
            writer.append(reader)
            total += len(reader.pages)
        writer.write(output)
    return total


def split_pdf(input, ranges, output_pattern, password=None):
    """Split `input` into one file per (start, end) range (0-based inclusive).

    Args:
        input: Source PDF path.
        ranges: List of (start, end) tuples, 0-based, end inclusive.
        output_pattern: Path pattern containing `{n}` (1-based part number),
            e.g. ``'out_{n}.pdf'``.
        password: Optional password for an encrypted source.

    Returns:
        list[str]: Paths of the written files, in range order.
    """
    if "{n" not in output_pattern:
        raise ValueError("output_pattern must contain a '{n}' placeholder.")
    outputs = []
    with _open_reader(input, password) as reader:
        page_count = len(reader.pages)
        for n, (start, end) in enumerate(ranges, start=1):
            _check_page_index(start, page_count, input)
            _check_page_index(end, page_count, input)
            if end < start:
                raise ValueError(f"Invalid page range ({start}, {end}): end before start.")
            writer = PdfWriter()
            for i in range(start, end + 1):
                writer.add_page(reader.pages[i])
            out_path = output_pattern.format(n=n)
            writer.write(out_path)
            outputs.append(out_path)
    return outputs


def extract_pages(input, pages, output, password=None):
    """Write the 0-based page indices `pages` of `input` to `output` in order."""
    pages = list(pages)
    if not pages:
        raise ValueError("At least one page index is required for extraction.")
    with _open_reader(input, password) as reader:
        page_count = len(reader.pages)
        writer = PdfWriter()
        for idx in pages:
            _check_page_index(idx, page_count, input)
            writer.add_page(reader.pages[idx])
        writer.write(output)


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------

def set_passwords(input, output, user_pw=None, owner_pw=None, allow_print=True,
                  allow_copy=False, allow_modify=False, algorithm="AES-256",
                  password=None):
    """Encrypt `input` to `output` with user/owner passwords and permissions.

    At least one of `user_pw` / `owner_pw` is required. When only `user_pw`
    is given the owner password defaults to it. When only `owner_pw` is
    given the file opens without a password but permissions still apply.

    Args:
        allow_print: Permit printing (including high-resolution printing).
        allow_copy: Permit text/graphics extraction (copy).
        allow_modify: Permit modification, assembly and form filling.
        algorithm: pypdf encryption algorithm name (default 'AES-256').
        password: Optional password of the (already encrypted) source file.
    """
    if not user_pw and not owner_pw:
        raise ValueError("At least one of user_pw or owner_pw is required.")
    if owner_pw is None:
        owner_pw = user_pw

    # Start from pypdf's "all allowed" default (reserved bits set correctly)
    # and clear what is not permitted.
    flags = UserAccessPermissions((1 << 32) - 4)
    if not allow_print:
        flags &= ~(UserAccessPermissions.PRINT
                   | UserAccessPermissions.PRINT_TO_REPRESENTATION)
    if not allow_copy:
        flags &= ~(UserAccessPermissions.EXTRACT
                   | UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS)
    if not allow_modify:
        flags &= ~(UserAccessPermissions.MODIFY
                   | UserAccessPermissions.ADD_OR_MODIFY
                   | UserAccessPermissions.FILL_FORM_FIELDS
                   | UserAccessPermissions.ASSEMBLE_DOC)

    with _open_reader(input, password) as reader:
        writer = PdfWriter(clone_from=reader)
        writer.encrypt(
            user_password=user_pw or "",
            owner_password=owner_pw,
            permissions_flag=UserAccessPermissions(flags),
            algorithm=algorithm,
        )
        writer.write(output)


def remove_password(input, password, output):
    """Write a decrypted copy of `input` to `output`.

    Raises:
        ValueError: If `password` is wrong.
    """
    with open(input, "rb") as fh:
        reader = PdfReader(fh)
        if reader.is_encrypted:
            try:
                result = reader.decrypt(password if password is not None else "")
            except Exception as exc:
                raise ValueError(f"Could not decrypt '{input}': {exc}") from exc
            if not result:
                raise ValueError(f"Incorrect password for '{input}'.")
        writer = PdfWriter(clone_from=reader)
        writer.write(output)


# ---------------------------------------------------------------------------
# Sanitizing
# ---------------------------------------------------------------------------

def sanitize(input, output, password=None, strip_metadata=True,
             strip_annotations=True, strip_attachments=True,
             strip_javascript=True):
    """Strip hidden/dangerous content from `input`, writing to `output`.

    Removes (depending on flags): XMP metadata stream and document info
    dictionary, document JavaScript, embedded file attachments, the
    /OpenAction entry, additional actions (/AA, document and page level)
    and page annotations.

    Returns:
        dict: ``{"removed": [description, ...]}`` listing what was removed.
    """
    removed = []
    with _open_reader(input, password) as reader:
        writer = PdfWriter(clone_from=reader)
        root = writer._root_object

        if strip_metadata:
            if "/Metadata" in root:
                del root["/Metadata"]
                removed.append("XMP metadata stream")
            if writer.metadata:
                writer.metadata = None
                removed.append("document information dictionary")

        names = root.get("/Names")
        names = names.get_object() if names is not None else None

        if strip_javascript:
            if names is not None and "/JavaScript" in names:
                del names["/JavaScript"]
                removed.append("document JavaScript")
            if "/OpenAction" in root:
                del root["/OpenAction"]
                removed.append("document OpenAction")
            if "/AA" in root:
                del root["/AA"]
                removed.append("document additional actions")

        if strip_attachments and names is not None and "/EmbeddedFiles" in names:
            del names["/EmbeddedFiles"]
            removed.append("embedded file attachments")

        if names is not None and len(names) == 0 and "/Names" in root:
            del root["/Names"]

        annot_pages = 0
        action_pages = 0
        for page in writer.pages:
            if strip_annotations and "/Annots" in page:
                del page["/Annots"]
                annot_pages += 1
            if strip_javascript and "/AA" in page:
                del page["/AA"]
                action_pages += 1
        if annot_pages:
            removed.append(f"annotations on {annot_pages} page(s)")
        if action_pages:
            removed.append(f"additional actions on {action_pages} page(s)")

        writer.write(output)
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Document properties
# ---------------------------------------------------------------------------

def read_properties(input, password=None):
    """Read document properties of `input`.

    Returns:
        dict with keys: title, author, subject, keywords, creator, producer,
        created, modified, pages, encrypted, page_size_pt. String values are
        None when absent; created/modified are datetimes when parseable, the
        raw PDF date string otherwise; page_size_pt is (width, height) of the
        first page in points or None for a zero-page document.
    """
    with _open_reader(input, password) as reader:
        encrypted = reader.is_encrypted
        meta = reader.metadata

        def text(key):
            if meta is None:
                return None
            value = meta.get(key)
            return str(value) if value is not None else None

        def date(prop, key):
            if meta is None or key not in meta:
                return None
            try:
                value = getattr(meta, prop)
            except Exception:
                value = None
            return value if value is not None else text(key)

        pages = len(reader.pages)
        page_size_pt = None
        if pages:
            box = reader.pages[0].mediabox
            page_size_pt = (float(box.width), float(box.height))

        return {
            "title": text("/Title"),
            "author": text("/Author"),
            "subject": text("/Subject"),
            "keywords": text("/Keywords"),
            "creator": text("/Creator"),
            "producer": text("/Producer"),
            "created": date("creation_date", "/CreationDate"),
            "modified": date("modification_date", "/ModDate"),
            "pages": pages,
            "encrypted": encrypted,
            "page_size_pt": page_size_pt,
        }


# Friendly metadata keys accepted by write_properties.
_INFO_KEYS = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
    "creator": "/Creator",
    "producer": "/Producer",
    "created": "/CreationDate",
    "modified": "/ModDate",
}


def write_properties(input, output, metadata, password=None):
    """Write `metadata` into a copy of `input` saved as `output`.

    `metadata` maps friendly keys (title, author, subject, keywords, creator,
    producer, created, modified) or raw PDF info keys ('/Title', ...) to
    values. datetime values are converted to PDF date strings. None values
    are skipped. Unicode is fully supported.
    """
    info = {}
    for key, value in dict(metadata).items():
        if value is None:
            continue
        pdf_key = key if key.startswith("/") else _INFO_KEYS.get(key.lower())
        if pdf_key is None:
            raise ValueError(f"Unknown metadata key: {key!r}")
        if isinstance(value, datetime):
            value = value.strftime("D:%Y%m%d%H%M%S")
        info[pdf_key] = str(value)
    with _open_reader(input, password) as reader:
        writer = PdfWriter(clone_from=reader)
        if info:
            writer.add_metadata(info)
        writer.write(output)


# ---------------------------------------------------------------------------
# Rotation convenience
# ---------------------------------------------------------------------------

def rotate_pages(input, output, rotations, password=None):
    """Rotate pages of `input` clockwise and write to `output`.

    Args:
        rotations: dict mapping 0-based page index to degrees (multiple of 90).
    """
    with _open_reader(input, password) as reader:
        page_count = len(reader.pages)
        for idx, degrees in rotations.items():
            _check_page_index(idx, page_count, input)
            if degrees % 90 != 0:
                raise ValueError(f"Rotation must be a multiple of 90, got {degrees}.")
            reader.pages[idx].rotate(degrees % 360)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.write(output)


# ---------------------------------------------------------------------------
# Page-organization journal
# ---------------------------------------------------------------------------

class PageOpsJournal:
    """Records page-organization operations for deferred, lossless replay.

    Each op's indices refer to the document state at the time the op was
    recorded (i.e. the current state after all previous ops). Supported ops:

    - ``('delete', [idx, ...])``            delete pages
    - ``('move', src, dst)``                pop page `src`, insert at `dst`
    - ``('rotate', {idx: degrees})``        rotate clockwise (90/180/270)
    - ``('insert_blank', idx, [w_pt, h_pt])``  insert a blank page
    - ``('insert_pdf', idx, src_path, [page_indices])``  insert pages of a PDF
    - ``('crop', idx, [x0, y0, x1, y1])``   crop; box in original-image px
                                            (200 PPI, y-down)

    The journal replays in two worlds:

    - :meth:`apply_to_images` mutates a list of ImageContainer-like objects
      in place (duck-typed, no image_container import).
    - :func:`apply_journal` replays the ops on the original PDF via pypdf.
    """

    def __init__(self, ops=None):
        self._ops = []
        for op in ops or []:
            self.record(op)

    # -- recording / serialization ------------------------------------------

    def record(self, op):
        """Validate and append `op` (see class docstring for shapes)."""
        op = tuple(op)
        if not op or op[0] not in _VALID_OPS:
            raise ValueError(f"Unknown journal op: {op!r}")
        kind = op[0]
        if kind == "delete":
            if len(op) != 2 or not op[1]:
                raise ValueError(f"Malformed delete op: {op!r}")
            indices = sorted({int(i) for i in op[1]})
            if any(i < 0 for i in indices):
                raise ValueError(f"Negative page index in delete op: {op!r}")
            self._ops.append(("delete", indices))
        elif kind == "move":
            if len(op) != 3:
                raise ValueError(f"Malformed move op: {op!r}")
            src, dst = int(op[1]), int(op[2])
            if src < 0 or dst < 0:
                raise ValueError(f"Negative page index in move op: {op!r}")
            self._ops.append(("move", src, dst))
        elif kind == "rotate":
            if len(op) != 2 or not isinstance(op[1], dict):
                raise ValueError(f"Malformed rotate op: {op!r}")
            rotations = {}
            for idx, degrees in op[1].items():
                idx, degrees = int(idx), int(degrees)
                if idx < 0 or degrees % 90 != 0:
                    raise ValueError(
                        f"Rotation must use a multiple of 90 degrees: {op!r}")
                degrees %= 360
                if degrees:
                    rotations[idx] = degrees
            if rotations:
                self._ops.append(("rotate", rotations))
        elif kind == "insert_blank":
            if len(op) != 3 or len(op[2]) != 2:
                raise ValueError(f"Malformed insert_blank op: {op!r}")
            idx = int(op[1])
            width_pt, height_pt = float(op[2][0]), float(op[2][1])
            if idx < 0 or width_pt <= 0 or height_pt <= 0:
                raise ValueError(f"Invalid insert_blank op: {op!r}")
            self._ops.append(("insert_blank", idx, [width_pt, height_pt]))
        elif kind == "insert_pdf":
            if len(op) != 4 or not op[2] or not op[3]:
                raise ValueError(f"Malformed insert_pdf op: {op!r}")
            idx = int(op[1])
            indices = [int(i) for i in op[3]]
            if idx < 0 or any(i < 0 for i in indices):
                raise ValueError(f"Negative index in insert_pdf op: {op!r}")
            self._ops.append(("insert_pdf", idx, str(op[2]), indices))
        elif kind == "crop":
            if len(op) != 3 or len(op[2]) != 4:
                raise ValueError(f"Malformed crop op: {op!r}")
            idx = int(op[1])
            x0, y0, x1, y1 = (float(v) for v in op[2])
            if idx < 0 or x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0:
                raise ValueError(f"Invalid crop box in op: {op!r}")
            self._ops.append(("crop", idx, [x0, y0, x1, y1]))

    @property
    def ops(self):
        """A copy of the recorded ops."""
        return list(self._ops)

    def is_empty(self):
        """True when no operations have been recorded."""
        return not self._ops

    def to_dict(self):
        """JSON-serializable form: ``{"ops": [[name, ...], ...]}``."""
        serialized = []
        for op in self._ops:
            serialized.append([
                dict(part) if isinstance(part, dict)
                else list(part) if isinstance(part, (list, tuple))
                else part
                for part in op
            ])
        return {"ops": serialized}

    @classmethod
    def from_dict(cls, data):
        """Rebuild from :meth:`to_dict` output (or a bare ops list).

        JSON round-trips stringify rotate-op keys; they are coerced back
        to integers by :meth:`record`.
        """
        if data is None:
            return cls()
        ops = data.get("ops", []) if isinstance(data, dict) else data
        return cls(ops)

    # -- replay on images ----------------------------------------------------

    def apply_to_images(self, images, callbacks=None):
        """Replay the journal on `images` (mutated in place).

        `images` is a list of ImageContainer-like objects, duck-typed: each
        needs `image` (PIL.Image), `width_in_pt`, `height_in_pt` and
        optionally `scaled_image`, `size`, `close()`, `scale_image()`.

        `callbacks` supplies factories for ops that create pages:

        - ``'make_blank'``: ``fn(width_pt, height_pt) -> container`` —
          required when the journal contains ``insert_blank`` ops.
        - ``'render_pdf_pages'``: ``fn(path, page_indices) -> list[container]``
          — required when the journal contains ``insert_pdf`` ops.

        (The architecture doc names this argument `render_pages_fn`; a dict
        of callbacks is used so blank-page creation and PDF rendering stay
        independent.)

        Rotation is clockwise, mirroring PDF /Rotate semantics; width/height
        in points are swapped for 90/270. Crops use original-image pixel
        boxes and update the point sizes via ``pt = px * 72/200``.
        """
        callbacks = callbacks or {}
        for op in self._ops:
            kind = op[0]
            if kind == "delete":
                for idx in sorted(op[1], reverse=True):
                    container = images.pop(idx)
                    close = getattr(container, "close", None)
                    if callable(close):
                        close()
            elif kind == "move":
                images.insert(op[2], images.pop(op[1]))
            elif kind == "rotate":
                for idx, degrees in op[1].items():
                    container = images[idx]
                    new_image = container.image.transpose(_CW_TRANSPOSE[degrees])
                    _replace_container_image(container, new_image)
                    if degrees in (90, 270):
                        _set_container_size(
                            container, container.height_in_pt, container.width_in_pt)
            elif kind == "insert_blank":
                make_blank = callbacks.get("make_blank")
                if not callable(make_blank):
                    raise ValueError(
                        "insert_blank ops require a 'make_blank' callback.")
                images.insert(op[1], make_blank(op[2][0], op[2][1]))
            elif kind == "insert_pdf":
                render = callbacks.get("render_pdf_pages")
                if not callable(render):
                    raise ValueError(
                        "insert_pdf ops require a 'render_pdf_pages' callback.")
                new_pages = list(render(op[2], op[3]))
                images[op[1]:op[1]] = new_pages
            elif kind == "crop":
                container = images[op[1]]
                x0, y0, x1, y1 = op[2]
                new_image = container.image.crop(
                    (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))))
                _replace_container_image(container, new_image)
                _set_container_size(
                    container, (x1 - x0) * PT_PER_PX, (y1 - y0) * PT_PER_PX)


def _crop_pdf_page(page, box_px):
    """Set media/crop box of `page` from a display-space pixel box.

    `box_px` is (x0, y0, x1, y1) in original-image pixels (200 PPI, y-down)
    relative to the page as currently DISPLAYED — i.e. after the current
    mediabox offset and /Rotate are applied. Coordinates are converted to
    points, y-flipped, and mapped back into unrotated PDF user space.
    """
    x0, y0, x1, y1 = (float(v) * PT_PER_PX for v in box_px)
    box = page.mediabox
    mx0, my0 = float(box.left), float(box.bottom)
    mx1, my1 = float(box.right), float(box.top)
    rotation = page.rotation % 360

    def to_user_space(xd, yd):
        if rotation == 90:
            return (mx0 + yd, my0 + xd)
        if rotation == 180:
            return (mx1 - xd, my0 + yd)
        if rotation == 270:
            return (mx1 - yd, my1 - xd)
        return (mx0 + xd, my1 - yd)

    ax, ay = to_user_space(x0, y0)
    bx, by = to_user_space(x1, y1)
    rect = RectangleObject([min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)])
    page.mediabox = rect
    page.cropbox = RectangleObject([min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)])


def apply_journal(input, journal, output, password=None):
    """Replay `journal` on the original PDF `input`, writing to `output`.

    Losslessly applies every op with pypdf: rotate uses /Rotate, crop sets
    both cropbox and mediabox (px converted to pt with y-flip, rotation and
    mediabox offsets respected), insert_blank adds an empty page of the given
    size in points, insert_pdf pulls pages from another file, delete/move
    manipulate the page sequence. Ops apply in order; indices always refer
    to the current (already partially edited) state.

    `journal` may be a PageOpsJournal, a dict from
    :meth:`PageOpsJournal.to_dict`, a bare ops list, or None (plain copy).
    """
    if isinstance(journal, PageOpsJournal):
        ops = journal.ops
    else:
        ops = PageOpsJournal.from_dict(journal).ops

    with ExitStack() as stack:
        fh = stack.enter_context(open(input, "rb"))
        reader = PdfReader(fh)
        _decrypt(reader, input, password)
        pages = list(reader.pages)

        for op in ops:
            kind = op[0]
            if kind == "delete":
                for idx in sorted(op[1], reverse=True):
                    _check_page_index(idx, len(pages), input)
                    pages.pop(idx)
            elif kind == "move":
                src, dst = op[1], op[2]
                _check_page_index(src, len(pages), input)
                if not 0 <= dst < len(pages):
                    raise ValueError(f"Move destination {dst} out of range.")
                pages.insert(dst, pages.pop(src))
            elif kind == "rotate":
                for idx, degrees in op[1].items():
                    _check_page_index(idx, len(pages), input)
                    pages[idx].rotate(degrees)
            elif kind == "insert_blank":
                idx, (width_pt, height_pt) = op[1], op[2]
                if not 0 <= idx <= len(pages):
                    raise ValueError(f"Insert position {idx} out of range.")
                pages.insert(
                    idx, PageObject.create_blank_page(width=width_pt, height=height_pt))
            elif kind == "insert_pdf":
                idx, src_path, indices = op[1], op[2], op[3]
                if not 0 <= idx <= len(pages):
                    raise ValueError(f"Insert position {idx} out of range.")
                # A fresh reader per op so repeated inserts of the same file
                # never share (and never double-mutate) page objects.
                src_fh = stack.enter_context(open(src_path, "rb"))
                src_reader = PdfReader(src_fh)
                _decrypt(src_reader, src_path, None)
                for i in indices:
                    _check_page_index(i, len(src_reader.pages), src_path)
                pages[idx:idx] = [src_reader.pages[i] for i in indices]
            elif kind == "crop":
                _check_page_index(op[1], len(pages), input)
                _crop_pdf_page(pages[op[1]], op[2])

        writer = PdfWriter()
        for page in pages:
            writer.add_page(page)
        writer.write(output)
