# CoverUP Acrobat-Suite Architecture Contracts

Binding contracts for the feature build-out on `feature/acrobat-suite`. Every module must
conform; integration relies on these exact shapes. Python: `.venv/bin/python` (3.13, Tk 8.6).

## Layering rule (enforced by tests/test_purity.py)

Business modules — `pdf_ops`, `annotations`, `convert`, `ocr`, `signing`, `forms`,
`compare`, `search` — never import FreeSimpleGUI or tkinter. GUI modules (`main`, `ui`,
`menu`, `dialogs/*`, `handlers/*`, `canvas_tools`, `thumbnails`, `tasks`, `state`) may
import business modules. All user-visible strings live in GUI modules via `i18n._()`;
business modules raise exceptions with plain-English messages and return data.

## Coordinates

Canvas/annotation coordinates are in ORIGINAL-image pixel space at 200 PPI
(`import_ppi = 200`), y-down, exactly like the existing rectangle model.
Conversions: `px = pt * 200/72`, `pt = px * 72/200`. PDF y-axis is flipped
(`y_pt = page_height_pt - y_px * 72/200`).

## AppState (`coverup/state.py`)

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

## Handler registry (`coverup/handlers/`)

Each group module (`file.py, edit.py, view.py, organize.py, protect.py, convert.py,
review.py, sign.py, annotate.py, help.py`) defines:

```python
HANDLERS: dict[str, Callable[[sg.Window, AppState], None]] = {...}
```

`coverup/handlers/__init__.py` imports every group and merges into one `HANDLERS` dict
(duplicate keys are a startup error). `main.py` dispatches menu events through it.
Handlers own their dialogs (call `coverup.dialogs.<group>`), validation, and calling
business functions — synchronously if fast, else via `tasks.run_task`.

## Menu (`coverup/menu.py`)

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

## Canvas tools (`coverup/canvas_tools.py`)

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

## Annotation model (`coverup/annotations.py`)

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
`coverup/fonts/` (resolve via `utils.find_fonts_folder`).

## Workfile v2 (`coverup/workfile.py`)

```json
{"version": 2, "annotations": [[{ann dict}...] per page], "decorations": {...},
 "journal": [...], "pages": N, "current_page": n, "fill_color": "black",
 "output_quality": "high"}
```
v1 files (`"rectangles"` key) migrate via `annotations.migrate_v1_rectangle`.
Passwords are never persisted.

## PageOpsJournal (`coverup/pdf_ops.py`)

Ops (indices refer to state at time of op): `('delete', [idx...])`, `('move', src, dst)`,
`('rotate', {idx: deg})`, `('insert_blank', idx, [w_pt, h_pt])`,
`('insert_pdf', idx, src_path, [page_indices])`, `('crop', idx, [x0,y0,x1,y1] px)`.
`record(op)`, `apply_to_images(images, render_pages_fn)`, `apply_to_pdf(reader_pages) ->
list of (source, transform)` consumed by `apply_journal(input, journal, output, password)`.
`to_dict()/from_dict()` for the workfile.

## Background tasks (`coverup/tasks.py`)

```python
run_task(window, fn, *args, key='-TASK-', **kwargs)
```
Daemon thread; worker communicates ONLY via
`window.write_event_value((key, 'PROGRESS'), (pct, msg))`, `((key,'DONE'), result)`,
`((key,'ERROR'), traceback_str)`. Business functions accept `progress_cb(pct, msg)`.
main.py handles these tuple events (progress bar, popup on error, completion callbacks
stored in a dict on state).

## Dialogs (`coverup/dialogs/`)

One module per group mirroring handlers. Each dialog: `modal=True, keep_on_top=True`,
centered via `coverup.dialogs.common.centered(parent_window)`, returns a plain request
dict or None. Shared helpers in `coverup/dialogs/common.py`: `centered`, `error_popup`,
`info_popup`, `file_open_row`, `parse_page_ranges("1-3,7,9-") -> list[int]` (0-based,
open-ended supported, raises ValueError with offending token).

## i18n

New user-visible strings: add `'en'` keys to `translations.py` (other languages fall
back automatically). Key naming: `menu_*`, `dlg_*`, `tool_*`, `err_*`, `msg_*`.

## Tests

`tests/fixtures.py` provides `make_pdf`, `make_text_pdf`, `make_encrypted_pdf`,
`make_form_pdf`, `make_scan_pdf`, `make_image`. Run: `.venv/bin/pytest tests/ -x -q`.
No binary fixtures in git. GUI never required (except test_gui_smoke which only
constructs layouts).
