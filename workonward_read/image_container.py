"""
ImageContainer class for managing PDF page images and typed annotations.

This module provides the ImageContainer class which wraps a PIL Image with
additional functionality for managing typed annotations (redactions, text,
highlights, shapes, stamps, images, signatures, …), zoom levels, and export
operations. Burn-in rendering is delegated to
:mod:`workonward_read.annotations` (``render_on_image``), which is also what the
ProcessPoolExecutor workers run at export time.

Each page of a loaded document is represented by an ImageContainer instance.
"""

import gc
import io
from concurrent.futures import ProcessPoolExecutor, wait as wait_futures

from PIL import Image

from workonward_read import annotations as annotations_engine
from workonward_read.utils import get_worker_count, find_fonts_folder, get_resource_root
from workonward_read.i18n import _


_FONT_DIR = None


def default_font_dir():
    """Resolve (and cache) the bundled DejaVu font folder for burn-in text."""
    global _FONT_DIR
    if _FONT_DIR is None:
        try:
            _FONT_DIR = find_fonts_folder(get_resource_root())
        except Exception:
            _FONT_DIR = ''
    return _FONT_DIR


def _finalize_page_worker(args):
    """
    Worker function for parallel page finalization. Runs in separate process.

    Args:
        args: Tuple of (page_index, image_bytes, ann_dicts, format, quality,
              scale, page_size, decorations, total_pages, font_dir).

    Returns:
        Tuple of (page_index, finalized_image_bytes, page_size) or
        (page_index, None, error_msg).
    """
    (page_index, image_bytes, ann_dicts, img_format, quality, scale,
     page_size, decorations, total_pages, font_dir) = args
    image = None
    input_buffer = None
    output_buffer = None
    try:
        # Reconstruct PIL image from bytes
        input_buffer = io.BytesIO(image_bytes)
        image = Image.open(input_buffer)
        # Load image data into memory so we can close the buffer
        image.load()

        # Burn annotations + document decorations into the page (returns a
        # new RGB image; the source image is never mutated).
        burned = annotations_engine.render_on_image(
            image, ann_dicts, decorations or {}, page_index, total_pages,
            page_size[0], page_size[1], font_dir)
        image.close()
        image = burned

        # Scale if needed
        if scale != 1:
            new_width = int(image.width * scale)
            new_height = int(image.height * scale)
            scaled_image = image.resize((new_width, new_height), resample=Image.Resampling.LANCZOS)
            image.close()
            image = scaled_image

        # Save to bytes (render_on_image already returned RGB)
        output_buffer = io.BytesIO()
        if img_format in ('JPEG', 'JPG'):
            image.save(output_buffer, format='JPEG', quality=quality, optimize=True)
        else:
            image.save(output_buffer, format='PNG')

        result_bytes = output_buffer.getvalue()
        return (page_index, result_bytes, page_size)
    except Exception as e:
        return (page_index, None, str(e))
    finally:
        # Ensure all resources are released
        if image is not None:
            try:
                image.close()
            except Exception:
                pass
        if input_buffer is not None:
            try:
                input_buffer.close()
            except Exception:
                pass
        if output_buffer is not None:
            try:
                output_buffer.close()
            except Exception:
                pass


class ImageContainer:
    """
    Container for images of PDF pages with typed annotation support.

    Manages a single page image along with its annotations, zoom state, and
    provides methods for drawing, exporting, and manipulating the page
    content.

    Attributes:
        image: Original PIL Image of the page.
        size: Tuple of PDF point dimensions for export (kept in the order it
              was handed in; the export path forwards it unchanged to FPDF).
        height_in_pt / width_in_pt: PDF point dimensions for export.
        scaled_image: Current scaled version of the image for display.
        id: Graph element ID when displayed.
        annotations: List of :class:`workonward_read.annotations.Annotation`.

    Class Attributes:
        zoom_factor: Shared zoom level (percentage) across all instances.
    """

    zoom_factor = 100

    def __init__(self, image, size=(0, 0), annotations=None):
        """
        Initialize an ImageContainer.

        Args:
            image: PIL Image object for this page.
            size: Tuple of PDF point dimensions.
            annotations: Optional list of existing Annotation objects.
        """
        self.image = image
        self.size = size
        self.height_in_pt = size[0]
        self.width_in_pt = size[1]
        self.scaled_image = self.image
        self.id = None
        # Monotonically increasing bitmap version: bumped whenever
        # self.image is swapped for a new object (rotate/crop via
        # pdf_ops._replace_container_image). Caches (thumbnails) key on it.
        self.image_version = 0

        # List of typed annotations for this page.
        self.annotations = list() if annotations is None else annotations

    def close(self):
        """Release all image resources held by this container."""
        # Close scaled image if it's different from original
        if self.scaled_image is not None and self.scaled_image is not self.image:
            try:
                self.scaled_image.close()
            except Exception:
                pass
            self.scaled_image = None
        # Close the original image
        if self.image is not None:
            try:
                self.image.close()
            except Exception:
                pass
            self.image = None

    def increase_zoom(self, number=20):
        """Zoom in on the image. Returns new zoom_factor."""
        ImageContainer.zoom_factor += number
        if ImageContainer.zoom_factor > 240:
            ImageContainer.zoom_factor = 240
        else:
            self.scale_image()
        return [ImageContainer.zoom_factor]

    def decrease_zoom(self, number=20):
        """Zoom out of the image. Returns new zoom_factor."""
        ImageContainer.zoom_factor -= number
        if ImageContainer.zoom_factor < 20:
            ImageContainer.zoom_factor = 20
        else:
            self.scale_image()
        return [ImageContainer.zoom_factor]

    def scale_image(self):
        """Scale original size image for display in Graph element."""
        # Close previous scaled image if it's not the original
        if self.scaled_image is not self.image:
            try:
                self.scaled_image.close()
            except Exception:
                pass
        width, height = self.image.size
        newwidth = int(width * ImageContainer.zoom_factor / 100)
        newheight = int(height * ImageContainer.zoom_factor / 100)
        self.scaled_image = self.image.resize((newwidth, newheight), resample=Image.Resampling.BILINEAR)

    def data(self):
        """Return bytes of scaled image."""
        with io.BytesIO() as output:
            self.scaled_image.save(output, format='PNG')
            data = output.getvalue()
            # Note: Don't cache - causes memory issues with large documents
            return data

    def display_data(self, decorations, page_idx, total_pages):
        """
        Return PNG bytes of the scaled image with document decorations
        (watermark, header/footer, page numbers, Bates) burned into a COPY
        for on-screen preview. Annotations are not included here — they are
        drawn as interactive graph figures. The original image data is never
        touched, so zoom and page flips stay lossless.

        The decoration metrics are rendered at the current display zoom
        (``decor_scale``) because they burn into the already zoom-scaled
        bitmap — otherwise the preview only matches the export at 100%.
        """
        if not decorations:
            return self.data()
        burned = annotations_engine.render_on_image(
            self.scaled_image, [], decorations, page_idx, total_pages,
            self.height_in_pt, self.width_in_pt, default_font_dir(),
            decor_scale=ImageContainer.zoom_factor / 100.0)
        with io.BytesIO() as output:
            burned.save(output, format='PNG')
            data = output.getvalue()
        burned.close()
        return data

    def jpg(self, image, image_quality=85, scale=1):
        """
        Return bytes of compressed JPEG image.

        Args:
            image: PIL Image to compress.
            image_quality: JPEG quality (1-100). Higher = better quality, larger file.
            scale: Scale factor for resizing (e.g., 0.64 for 96 DPI from 150 DPI).

        Returns:
            bytes: JPEG image data.
        """
        scaled_image = None
        with io.BytesIO() as output:
            if scale == 1:
                image_to_save = image
            else:
                scaled_image = image.resize(
                    (int(image.width * scale), int(image.height * scale)),
                    resample=Image.Resampling.LANCZOS
                )
                image_to_save = scaled_image
            image_to_save.save(output, format='JPEG', quality=image_quality, optimize=True)
            data = output.getvalue()
        # Close scaled image if we created one
        if scaled_image is not None:
            scaled_image.close()
        return data

    def refresh(self):
        """Update the scaled image and return self."""
        self.scale_image()
        return self

    def add_annotation(self, kind, props):
        """
        Create an Annotation of ``kind`` with ``props`` (original-image px
        coordinates), append it to this page and return it. Rendering to the
        graph is the caller's job (see canvas_tools.commit_annotation and
        draw_annotations_on_graph).
        """
        ann = annotations_engine.Annotation(
            id=annotations_engine.new_id(), kind=kind, props=props)
        self.annotations.append(ann)
        return ann

    def finalized_image(self, format='PIL', image_quality=92, scale=1,
                        decorations=None, page_idx=0, total_pages=1):
        """
        Return a copy of the imported image with all annotations (and
        optional document decorations) burned in.

        Args:
            format: Output format - 'PIL' returns PIL Image, 'JPEG'/'JPG' returns bytes.
            image_quality: JPEG quality (1-100). Default 92 for high quality.
            scale: Scale factor for DPI adjustment. Use 0.64 for ~96 DPI from 150 DPI import.
            decorations: Optional document-level decorations dict.
            page_idx: 0-based index of this page in the document.
            total_pages: Total page count of the document.

        Returns:
            PIL Image (RGB) or bytes depending on format parameter.
        """
        ann_dicts = [annotations_engine.to_dict(a) for a in self.annotations]
        final_image = annotations_engine.render_on_image(
            self.image, ann_dicts, decorations or {}, page_idx, total_pages,
            self.height_in_pt, self.width_in_pt, default_font_dir())
        if format in ('JPEG', 'JPG'):
            result = self.jpg(final_image, image_quality, scale)
            final_image.close()
            return result
        else:
            return final_image

    def draw_annotations_on_graph(self, window):
        """Draw all annotations to the graph at the current zoom.

        Old figure ids are deleted first to prevent accumulation; the new
        figure ids are stored back on each Annotation (transient).
        """
        graph = window['-GRAPH-']
        for ann in self.annotations:
            for figure_id in (ann.graph_ids or []):
                try:
                    graph.delete_figure(figure_id)
                except Exception:
                    pass
            ann.graph_ids = []
            try:
                annotations_engine.render_on_graph(
                    graph, ann, ImageContainer.zoom_factor)
            except Exception:
                ann.graph_ids = []


def export_annotations(pages):
    """
    Export all annotations from all pages for serialization.

    Args:
        pages: List of ImageContainer instances.

    Returns:
        list: List of annotation-dict lists, one per page, or None if no
              annotations exist or if pages is empty/None.
    """
    if not pages:
        return None

    try:
        annotations = [
            [annotations_engine.to_dict(a) for a in page.annotations]
            for page in pages
        ]
        if any(annotations):
            return annotations
        return None
    except (AttributeError, TypeError):
        return None


def close_all_pages(pages):
    """
    Close all ImageContainer instances and release their image resources.

    Call this before loading a new document to prevent memory leaks.

    Args:
        pages: List of ImageContainer instances.
    """
    if not pages:
        return

    for page in pages:
        if hasattr(page, 'close'):
            try:
                page.close()
            except Exception:
                pass

    # Clear the list
    pages.clear()
    gc.collect()


def delete_all_annotations(pages, delete_workfile_func):
    """
    Delete all annotations from all pages.

    Args:
        pages: List of ImageContainer instances.
        delete_workfile_func: Callback function to delete the associated workfile.

    Returns:
        bool: True if successful, False if pages was empty or None.
    """
    if not pages:
        return False

    try:
        for page in pages:
            if hasattr(page, 'annotations'):
                page.annotations = []

        if callable(delete_workfile_func):
            delete_workfile_func()

        return True
    except Exception:
        return False


def finalize_pages_chunked(pages, img_format='JPEG', quality=92, scale=1,
                           chunk_size=50, progress_callback=None,
                           decorations=None):
    """
    Finalize pages in chunks using multiprocessing, yielding results progressively.

    This generator processes pages in chunks to limit memory usage. Each chunk is
    prepared, processed in parallel, and yielded before moving to the next chunk.
    This prevents holding all pages in memory simultaneously.

    Progress is reported in two phases per page:
    - Phase 1 (0.5 per page): Preparation/serialization
    - Phase 2 (0.5 per page): Parallel processing

    Args:
        pages: List of ImageContainer instances.
        img_format: Output format ('JPEG' or 'PNG').
        quality: JPEG quality (1-100).
        scale: Scale factor for DPI adjustment.
        chunk_size: Number of pages to process per chunk (default: 50).
        progress_callback: Optional callback(completed, total) for progress updates.
                          Called with float values to support half-page increments.
        decorations: Optional document-level decorations dict burned into
                     every page (watermark, header/footer, page numbers,
                     Bates). Must be picklable (plain JSON-like dict).

    Yields:
        Tuples of (image_bytes, page_size) in page order.

    Raises:
        ValueError: If pages is empty/None or if any page fails to process.
    """
    if not pages:
        raise ValueError(_('error_no_pages'))

    total_pages = len(pages)
    max_workers = get_worker_count(max_tasks=min(chunk_size, total_pages))
    completed_total = 0.0
    decorations = decorations or {}
    font_dir = default_font_dir()

    # Process pages in chunks
    for chunk_start in range(0, total_pages, chunk_size):
        chunk_end = min(chunk_start + chunk_size, total_pages)
        chunk_pages = pages[chunk_start:chunk_end]

        # Prepare arguments for this chunk (Phase 1: ~50% of work per page)
        worker_args = []
        for i, page in enumerate(chunk_pages):
            page_idx = chunk_start + i

            # Convert image to bytes (use context manager for proper cleanup)
            with io.BytesIO() as buffer:
                page.image.save(buffer, format='JPEG')
                image_bytes = buffer.getvalue()

            # Extract picklable annotation dicts (graph ids are dropped)
            ann_dicts = [annotations_engine.to_dict(a) for a in page.annotations]

            worker_args.append((
                page_idx,
                image_bytes,
                ann_dicts,
                img_format,
                quality,
                scale,
                (page.height_in_pt, page.width_in_pt),
                decorations,
                total_pages,
                font_dir,
            ))

            # Clear reference to allow GC of this page's bytes before next iteration
            del image_bytes
            del ann_dicts

            # Report preparation progress (half credit per page)
            completed_total += 0.5
            if progress_callback:
                progress_callback(completed_total, total_pages)

        # Clear reference to chunk_pages slice
        del chunk_pages

        # Process this chunk in parallel (Phase 2: ~50% of work per page)
        chunk_results = [None] * (chunk_end - chunk_start)

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_finalize_page_worker, args): args[0] for args in worker_args}

            # Clear worker_args immediately after submitting - executor has copies
            del worker_args
            gc.collect()

            pending = set(futures.keys())

            while pending:
                done, pending = wait_futures(pending, timeout=0.05)

                for future in done:
                    result = future.result()
                    page_idx = result[0]
                    chunk_idx = page_idx - chunk_start

                    if result[1] is None:
                        raise ValueError(_('error_page_process_failed', page=page_idx + 1, error=result[2]))

                    chunk_results[chunk_idx] = (result[1], result[2])

                    # Report processing progress (remaining half credit per page)
                    completed_total += 0.5
                    if progress_callback:
                        progress_callback(completed_total, total_pages)

            # Clear futures dict
            del futures

        # Yield results from this chunk in order, then release memory
        for i, result in enumerate(chunk_results):
            yield result
            # Clear each result after yielding to free memory immediately
            chunk_results[i] = None

        # Force garbage collection after each chunk
        # This is critical for large documents to prevent memory exhaustion
        del chunk_results
        gc.collect()
