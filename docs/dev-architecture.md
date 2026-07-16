# WorkOnward Read Acrobat-Suite Architecture Contracts

Binding contracts for the feature build-out on `feature/acrobat-suite`. Every module must
conform; integration relies on these exact shapes. Python: `.venv/bin/python` (3.13, Tk 8.6).

## Layering rule (enforced by tests/test_purity.py)

Business modules — `pdf_ops`, `annotations`, `convert`, `ocr`, `signing`, `forms`,
`compare`, `search`, `geometry`, `pdfium_io` — never import FreeSimpleGUI or tkinter. GUI modules (`main`, `ui`,
`menu`, `dialogs/*`, `handlers/*`, `canvas_tools`, `thumbnails`, `tasks`, `state`) may
import business modules. All user-visible strings live in GUI modules via `i18n._()`;
business modules raise exceptions with plain-English messages and return data.

## Coordinates

Canvas/annotation coordinates are in ORIGINAL-image pixel space at 200 PPI
(`import_ppi = 200`), y-down, exactly like the existing rectangle model.
Conversions: `px = pt * 200/72`, `pt = px * 72/200`. PDF y-axis is flipped
(`y_pt = page_height_pt - y_px * 72/200`).

LEGACY container size convention: `ImageContainer.size` keeps the order the
loader handed in — pdfium's `page.get_size()` = `(width_pt, height_pt)` — so
`size[0]` is the page WIDTH in pt and is stored in `height_in_pt`, while
`size[1]` (the HEIGHT) lives in `width_in_pt`. The attribute names are
historic; the export path relies on the convention
(`handlers/file.py: add_page(format=(height_in_pt, width_in_pt))`). Every
code path that resizes a page (rotate/crop in `pdf_ops.apply_to_images`)
must preserve it.

## Geometry (`workonward_read/geometry.py`)

Pure pixel-space transform helpers (business module):

```python
rotate_point_cw(pt, degrees, old_w, old_h) -> [x, y]   # CW, pixel-index conv.
translate_point(pt, dx, dy) -> [x, y]
transform_annotation(ann_or_dict, op) -> ann | None    # mutates geometry props
transform_annotations(list, op) -> list                # drops removed anns
transform_rect(rect, ops, page_w, page_h) -> rect | None
```

`op` is `('rotate', degrees, old_w, old_h)` or `('crop', (x0, y0, x1, y1))`.
`transform_annotation` covers every kind's geometry props: `p1`/`p2`
(boxed kinds re-normalized; line/arrow keep direction), `pos`, `points`,
and rotation adjusts the stamp `angle`. Crops drop annotations fully outside
the box, clamp partially-outside boxed kinds, and NEVER shrink a `redact`
below its intersection with the new page (coverage is preserved).
`transform_rect` consumes the per-page op lists produced by
`PageOpsJournal.transform_ops_for_original` (used for search-hit remapping).

## AppState (`workonward_read/state.py`)

```python
@dataclass
class AppState:
    images: list = field(default_factory=list)        # list[ImageContainer]
    file_path: str | None = None                      # original document path
    source_password: str | None = None                # never persisted
    current_page: int = 0
    fill_color: str = 'black'
    output_quality: str = 'high'
    tool: str = 'redact'                              # current canvas tool key
    tool_props: dict = field(default_factory=dict)    # per-tool props (color, width, alpha…)
    decorations: dict = field(default_factory=dict)   # watermark/header_footer/page_numbers/bates
    journal: object | None = None                     # pdf_ops.PageOpsJournal
    undo: dict = field(default_factory=dict)          # page_idx -> annotations.UndoStack
    first_load: bool = True
    busy: bool = False
    thumbnails_visible: bool = False
```

Runtime-only helpers (not persisted): `import_ppi`, `workfile_manager`,
`icons`, plus the orchestration fields:

- `doc_lock: set` — busy set of reasons for background tasks currently
  USING the loaded document (`state.images` / `state.journal`), managed via
  `state.acquire_doc(reason)` / `state.release_doc(reason)`. Document-
  MUTATING and document-CONSUMING entry points (organize page ops,
  OCR-current-doc, save/export/print, open) call
  `dialogs.common.require_document_free(window, state)` first; while the set
  is non-empty they refuse with an info popup ("Another operation is using
  the document — wait for it to finish."). File->file tools (merge / split /
  extract / compress / batch on picked files) stay allowed concurrently.
  The legacy `busy` bool is superseded by `doc_lock` and no longer written.
- `aux_windows: dict` — the aux-window registry (see "Aux windows" below).
- `state.password_for(path)` — returns `source_password` when `path` is the
  loaded document (`os.path.abspath` + `os.path.normcase` comparison against
  `file_path`), else None. The single helper used by review / convert /
  organize / protect / sign for password-for-loaded-file resolution.

## Handler registry (`workonward_read/handlers/`)

Each group module (`file.py, edit.py, view.py, organize.py, protect.py, convert.py,
review.py, sign.py, annotate.py, help.py`) defines:

```python
HANDLERS: dict[str, Callable[[sg.Window, AppState], None]] = {...}
```

`workonward_read/handlers/__init__.py` imports every group and merges into one `HANDLERS` dict
(duplicate keys are a startup error). `main.py` dispatches menu events through it.
Handlers own their dialogs (call `workonward_read.dialogs.<group>`), validation, and calling
business functions — synchronously if fast, else via `tasks.run_task`.

## Menu (`workonward_read/menu.py`)

Items are `'<translated label>::<KEY>'`; keys are stable. Full key set:

- File: MENU_OPEN, MENU_SAVE_REDACTED, MENU_EXPORT_PAGE, MENU_SAVE_ORGANIZED,
  MENU_IMAGES_TO_PDF, MENU_CONVERT_IMAGES, MENU_CONVERT_TEXT, MENU_CONVERT_WORD,
  MENU_CONVERT_HTML, MENU_PRINT, MENU_EXIT
- Edit: MENU_UNDO, MENU_REDO, MENU_DELETE_ALL, MENU_SEARCH
- View: MENU_ZOOM_IN, MENU_ZOOM_OUT, MENU_PREV_PAGE, MENU_NEXT_PAGE, MENU_THUMBNAILS
- Tools: MENU_MERGE, MENU_SPLIT, MENU_INSERT_PAGES, MENU_DELETE_PAGES,
  MENU_REORDER_PAGES, MENU_ROTATE_PAGES, MENU_EXTRACT_PAGES, MENU_CROP,
  MENU_WATERMARK, MENU_HEADER_FOOTER, MENU_COMPRESS, MENU_OCR, MENU_COMPARE,
  MENU_BATCH, MENU_PROPERTIES
- Protect: MENU_SET_PASSWORDS, MENU_REMOVE_PASSWORD, MENU_SANITIZE
- Sign: MENU_FILL_SIGN, MENU_CERT_SIGN, MENU_VALIDATE_SIGS, MENU_FILL_FORM
- Help: MENU_ABOUT

`main.py` normalizes events: `if isinstance(event, str) and '::' in event:
event = event.rsplit('::', 1)[-1]`.

## Canvas tools (`workonward_read/canvas_tools.py`)

```python
class CanvasTool(Protocol):
    cursor: str                       # Tk cursor name while active
    def on_press(self, window, state, x, y): ...
    def on_drag(self, window, state, x, y): ...     # called per -GRAPH- drag event
    def on_release(self, window, state, x, y): ...  # -GRAPH-+UP

TOOLS: dict[str, CanvasTool]  # keys: redact, eraser, text, highlight, underline,
                              # strike, ink, rect, ellipse, line, arrow, stamp,
                              # image, signature, measure
```

Coordinates arriving at tools are already zoom-corrected to original-image px
(main.py divides by `zoom_factor/100` before dispatch) and y-flipped to y-down.
Tools that add annotations must push an undo snapshot first
(`state.undo.setdefault(p, UndoStack()).push(...)`).

## Annotation model (`workonward_read/annotations.py`)

`Annotation(id: str, kind: str, props: dict, graph_ids: list)` — `graph_ids` transient.
Kinds & required props (all coords original-image px):

- `redact`: p1, p2, fill ('black'|'white')
- `text`: pos [x,y], text, size_px, color, bold(bool)
- `highlight`: p1, p2, color ('#ffff00' default), alpha (0.4 default)
- `underline` / `strike`: p1, p2, color, width_px
- `ink`: points [[x,y]...], color, width_px
- `rect` / `ellipse`: p1, p2, outline, fill (None allowed), width_px
- `line` / `arrow`: p1, p2, color, width_px
- `stamp`: pos, preset ('approved'|'draft'|'confidential'|'custom'), text, color, angle, scale
- `image` / `signature`: pos, png_b64, scale

Key functions (pure, picklable args):
```python
to_dict(ann) -> dict;  from_dict(d) -> Annotation
migrate_v1_rectangle(t) -> Annotation          # (p1, p2, color, graph_id) tuple
render_on_image(pil_image, ann_dicts, decorations, page_idx, total_pages,
                page_w_pt, page_h_pt, font_dir) -> PIL.Image   # burn-in, RGBA for alpha
hit_test(annotations, x, y) -> Annotation | None
class UndoStack: push(list[Annotation]); undo() -> list|None; redo() -> list|None  # maxlen 25
```
Decorations dict (document-level): keys `watermark {text|png_b64, opacity, angle, scale}`,
`header_footer {left, center, right, position('header'|'footer'), size_px}`,
`page_numbers {template, start_at, position}`, `bates {prefix, start, digits, position}`.
Templates substitute `{page} {total} {date} {bates}`. Fonts: DejaVuSans[-Bold].ttf in
`workonward_read/fonts/` (resolve via `utils.find_fonts_folder`).

## Workfile v2 (`workonward_read/workfile.py`)

```json
{"version": 2, "annotations": [[{ann dict}...] per page], "decorations": {...},
 "journal": [...], "pages": N, "current_page": n, "fill_color": "black",
 "output_quality": "high"}
```
v1 files (`"rectangles"` key) migrate via `annotations.migrate_v1_rectangle`.
Passwords are never persisted.

`pages` and the per-page `annotations` lists describe the document AFTER the
saved `journal` was applied (they mirror `state.images` at save time), and
annotation coordinates are post-op. Restore therefore (a) accepts a session
when `pages == journal.page_count_after(original_page_count)`
(`document_loader.restored_session_matches`) and (b) replays the journal on
the freshly rendered original pages BEFORE attaching the restored
annotations (`document_loader.apply_restored_session`). `handlers/file.py`
then seeds each restored page's UndoStack with one empty-state snapshot so a
single Undo removes the restored set and Redo brings it back.

## PageOpsJournal (`workonward_read/pdf_ops.py`)

Ops (indices refer to state at time of op): `('delete', [idx...])`, `('move', src, dst)`,
`('rotate', {idx: deg})`, `('insert_blank', idx, [w_pt, h_pt])`,
`('insert_pdf', idx, src_path, [page_indices])`, `('crop', idx, [x0,y0,x1,y1] px)`.
`record(op)`, `apply_to_images(images, render_pages_fn)`, `apply_to_pdf(reader_pages) ->
list of (source, transform)` consumed by `apply_journal(input, journal, output, password)`.
`to_dict()/from_dict()` for the workfile.

`apply_to_images` also remaps each affected container's `annotations`
through rotate/crop ops (via `geometry.transform_annotations`) and keeps the
legacy container size convention (`size[0]` = WIDTH in pt, stored in
`height_in_pt`). The insert-op page factories live in
`page_render.JOURNAL_CALLBACKS` (re-exported by `handlers.organize`).

Page-identity helpers (no images touched):
`simulate_pages(original_count) -> [{'original': idx|None, 'ops': [...]}]`,
`page_count_after(original_count)`, `map_original_index(idx, original_count)
-> current|None`, `transform_ops_for_original(idx, original_count) ->
[('rotate', deg) | ('crop', box)] | None` — used by the restore guard, by
undo-stack remapping in `handlers.organize.record_and_apply`, and by
search-hit remapping (`handlers.review.remap_hit_location`, which feeds
`geometry.transform_rect` with `search.Hit.page_size_px`).

## Background tasks (`workonward_read/tasks.py`)

```python
run_task(window, fn, *args, on_done=None, on_error=None, **kwargs)
    -> threading.Thread   # thread.task_key = ('-TASK-', seq)
```
Every invocation mints a UNIQUE key `('-TASK-', seq)` (monotonic sequence
counter — deterministic, never reused), so concurrent tasks cannot collide.
The daemon-thread worker communicates ONLY via
`window.write_event_value((key, 'PROGRESS'), (pct, msg))`, `((key,'DONE'),
result)`, `((key,'ERROR'), traceback_str)`. Business functions accept
`progress_cb(pct, msg)`. Callers always pass the MAIN window.

`on_done(window, state, result)` / `on_error(window, state, traceback_str)`
are registered by `run_task` itself in a registry owned by `tasks`
(`tasks.pop_callbacks(key)`); handlers never touch a shared registry key.
main.py routes any event matching `tasks.is_task_event(event)` — from ANY
window — through `_handle_task_event`: PROGRESS updates the shared
`-PROGRESS-` bar (concurrent tasks: last progress report wins), ERROR runs
the `on_error` cleanup hook first and then shows the standard error popup,
DONE invokes `on_done`. Tasks that consume the loaded document additionally
hold a `state.doc_lock` reason for their whole duration, released in both
`on_done` and `on_error` (see AppState above).

## Aux windows (non-modal secondary windows)

The main event loop reads ALL open windows via `sg.read_all_windows()`
(no timeout — blocks exactly like `window.read()`; `write_event_value`
events are per-window queued and arrive attributed to the window they were
written to). Non-modal secondary windows follow this contract:

```python
state.aux_windows: dict[sg.Window, handler_fn]
handler_fn(window, state, event, values) -> bool   # True = keep open
```

The opening handler creates the window (`modal=False`, finalized),
registers it in `state.aux_windows` and RETURNS IMMEDIATELY — no nested
read loops. main.py routes each event to the owning window's handler; when
the handler returns falsy (or the window is closed / unregistered) the loop
closes and unregisters it. Background work started from an aux handler runs
via `tasks.run_task` against the MAIN window; the DONE/ERROR callbacks must
check `aux_window.was_closed()` before touching its elements. Current aux
windows: the search finder and the compare results window
(`handlers/review.py`). Modal dialogs (`dialogs/*`) are intentionally
blocking and stay as-is.

## Dialogs (`workonward_read/dialogs/`)

One module per group mirroring handlers. Each dialog: `modal=True, keep_on_top=True`,
centered via `workonward_read.dialogs.common.centered(parent_window)`, returns a plain request
dict or None. Shared helpers in `workonward_read/dialogs/common.py`: `centered`, `error_popup`,
`info_popup`, `require_document_free`, `file_open_row`,
`parse_page_ranges("1-3,7,9-") -> list[int]` (0-based,
open-ended supported, raises ValueError with offending token).

## pdfium access (`workonward_read/pdfium_io.py`)

pdfium is NOT thread-safe; every in-process pdfium call sequence must hold
the module-level `PDFIUM_LOCK` (an `RLock`). The business module
`pdfium_io` owns the lock and the ONE canonical open helper:

```python
PDFIUM_LOCK                                  # threading.RLock
PASSWORD_ERROR                               # canonical message
open_pdf(path, password=None) -> PdfDocument # FileNotFoundError / ValueError
close_pdf(doc)                               # close under lock, best effort
pdfium_session(path, password=None)          # ctx mgr: doc under the lock
page_count(doc); get_page_size(doc, index)
render_page_to_pil(doc_or_path, index, scale,
                   jpeg_roundtrip=False, grayscale=False, password=None)
```

`search`, `compare`, `convert`, `page_render`, `ocr` and `document_loader`
open PDFs exclusively through `pdfium_io` (password failures raise
`ValueError(PASSWORD_ERROR)`, missing files `FileNotFoundError`, other open
failures `ValueError('Could not open PDF: …')`). Long render/extract loops
acquire the lock PER PAGE so they don't starve other pdfium users.
Exception: `document_loader._render_pdf_page` runs in ProcessPoolExecutor
worker PROCESSES — each has its own pdfium instance and address space, so
no lock is needed (nor shareable) there.

## i18n

`i18n._(key)` returns the key itself when no translation exists. Convention for ALL
new feature strings: pass the final English text as the key, e.g.
`_('Merge PDFs…')`. Do NOT edit `translations.py` (avoids conflicts; other languages
fall back to English, which is the accepted v1 behavior). Existing keys
(`tooltip_*`, `error_*`, …) keep working unchanged.

## Tests

`tests/fixtures.py` provides `make_pdf`, `make_text_pdf`, `make_encrypted_pdf`,
`make_form_pdf`, `make_scan_pdf`, `make_image`. Run: `.venv/bin/pytest tests/ -x -q`.
No binary fixtures in git. GUI never required (except test_gui_smoke which only
constructs layouts).
