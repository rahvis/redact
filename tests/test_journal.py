"""
Tests for coverup.pdf_ops.PageOpsJournal and apply_journal.

Randomized (seeded) op sequences are replayed both on lightweight fake
ImageContainer-likes and on a real synthesized PDF, then the two worlds are
compared: page count, per-page rotation and effective page dimensions.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import json
import random

import pytest
import fixtures  # noqa: F401  (path setup / consistency with other suites)
from PIL import Image
from pypdf import PdfReader, PdfWriter

from coverup.pdf_ops import PT_PER_PX, PX_PER_PT, PageOpsJournal, apply_journal


def px(pt):
    """Points -> original-image pixels at 200 PPI."""
    return round(pt * PX_PER_PT)


class FakePage:
    """Duck-typed stand-in for ImageContainer (never imports the real one)."""

    def __init__(self, width_pt, height_pt, color="white"):
        self.image = Image.new("RGB", (px(width_pt), px(height_pt)), color)
        self.scaled_image = self.image
        self.width_in_pt = width_pt
        self.height_in_pt = height_pt
        self.size = (height_pt, width_pt)
        self.closed = False

    def close(self):
        self.closed = True
        if self.image is not None:
            self.image.close()
            self.image = None
        self.scaled_image = None


def make_sized_pdf(path, sizes):
    """Blank PDF whose page i has size sizes[i] = (width_pt, height_pt)."""
    writer = PdfWriter()
    for width_pt, height_pt in sizes:
        writer.add_blank_page(width=width_pt, height=height_pt)
    writer.write(str(path))
    return str(path)


def effective_display_size(page):
    """(width_pt, height_pt) of a pypdf page as displayed (rotation applied)."""
    rotation = page.rotation % 360
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    if rotation in (90, 270):
        return (height, width)
    return (width, height)


# ---------------------------------------------------------------------------
# Unit behaviour
# ---------------------------------------------------------------------------

def test_is_empty_and_record():
    journal = PageOpsJournal()
    assert journal.is_empty()
    journal.record(("rotate", {1: 90}))
    assert not journal.is_empty()
    assert journal.ops == [("rotate", {1: 90})]


def test_record_validation_errors():
    journal = PageOpsJournal()
    with pytest.raises(ValueError):
        journal.record(("frobnicate", 1))
    with pytest.raises(ValueError):
        journal.record(("rotate", {0: 45}))
    with pytest.raises(ValueError):
        journal.record(("crop", 0, [100, 100, 50, 200]))  # x1 < x0
    with pytest.raises(ValueError):
        journal.record(("insert_blank", 0, [-10, 100]))
    with pytest.raises(ValueError):
        journal.record(("delete", []))
    with pytest.raises(ValueError):
        journal.record(("move", -1, 0))
    assert journal.is_empty()


def test_rotate_360_is_dropped():
    journal = PageOpsJournal()
    journal.record(("rotate", {0: 360}))
    assert journal.is_empty()


def test_serialization_roundtrip_through_json():
    journal = PageOpsJournal()
    journal.record(("delete", [2, 0]))
    journal.record(("move", 1, 0))
    journal.record(("rotate", {0: 90, 3: 270}))
    journal.record(("insert_blank", 1, [200, 300]))
    journal.record(("insert_pdf", 0, "/tmp/some.pdf", [0, 2]))
    journal.record(("crop", 2, [10, 20, 110, 220]))

    payload = json.loads(json.dumps(journal.to_dict()))
    restored = PageOpsJournal.from_dict(payload)

    assert restored.ops == journal.ops
    # rotate keys must be ints again after the JSON round trip
    rotate_op = [op for op in restored.ops if op[0] == "rotate"][0]
    assert all(isinstance(k, int) for k in rotate_op[1])
    # bare-list form is accepted too
    assert PageOpsJournal.from_dict(payload["ops"]).ops == journal.ops
    assert PageOpsJournal.from_dict(None).is_empty()


def test_apply_to_images_rotate_direction_and_swap():
    fake = FakePage(200, 400)
    # red marker at the top-left corner
    fake.image.putpixel((0, 0), (255, 0, 0))
    old_w, old_h = fake.image.size

    journal = PageOpsJournal()
    journal.record(("rotate", {0: 90}))
    images = [fake]
    journal.apply_to_images(images, {})

    assert images[0].image.size == (old_h, old_w)
    # clockwise: top-left corner moves to the top-right corner
    assert images[0].image.getpixel((old_h - 1, 0)) == (255, 0, 0)
    assert images[0].width_in_pt == 400
    assert images[0].height_in_pt == 200
    assert images[0].size == (200, 400)


def test_apply_to_images_delete_closes_and_move_reorders():
    images = [FakePage(100 + 10 * i, 300) for i in range(4)]  # widths 100..130
    deleted = [images[0], images[2]]
    journal = PageOpsJournal()
    journal.record(("delete", [2, 0]))   # -> widths [110, 130]
    journal.record(("move", 1, 0))       # -> widths [130, 110]
    journal.apply_to_images(images, {})

    assert [im.width_in_pt for im in images] == [130, 110]
    assert all(page.closed for page in deleted)
    assert not any(page.closed for page in images)


def test_apply_to_images_crop_updates_pixels_and_points():
    fake = FakePage(360, 720)  # 1000 x 2000 px at 200 PPI
    journal = PageOpsJournal()
    journal.record(("crop", 0, [100, 200, 600, 1200]))
    journal.apply_to_images([fake], {})

    assert fake.image.size == (500, 1000)
    assert fake.width_in_pt == pytest.approx(500 * PT_PER_PX)
    assert fake.height_in_pt == pytest.approx(1000 * PT_PER_PX)


def test_apply_to_images_requires_callbacks():
    journal = PageOpsJournal()
    journal.record(("insert_blank", 0, [100, 100]))
    with pytest.raises(ValueError):
        journal.apply_to_images([], {})

    journal2 = PageOpsJournal()
    journal2.record(("insert_pdf", 0, "x.pdf", [0]))
    with pytest.raises(ValueError):
        journal2.apply_to_images([], {"make_blank": FakePage})


# ---------------------------------------------------------------------------
# apply_journal on real PDFs
# ---------------------------------------------------------------------------

def test_crop_y_flip_known_rectangle(tmp_path):
    src = make_sized_pdf(tmp_path / "src.pdf", [(600, 800)])
    out = str(tmp_path / "out.pdf")

    journal = PageOpsJournal()
    journal.record(("crop", 0, [100, 200, 500, 700]))
    apply_journal(src, journal, out)

    page = PdfReader(out).pages[0]
    # px * 72/200: x: 36..180; y flipped: 800-700*0.36=548 .. 800-200*0.36=728
    expected = [36.0, 548.0, 180.0, 728.0]
    for value, want in zip(page.mediabox, expected):
        assert float(value) == pytest.approx(want, abs=1e-3)
    for value, want in zip(page.cropbox, expected):
        assert float(value) == pytest.approx(want, abs=1e-3)


def test_apply_journal_empty_copies_input(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=2)
    out = str(tmp_path / "out.pdf")
    apply_journal(src, PageOpsJournal(), out)
    assert len(PdfReader(out).pages) == 2


def test_apply_journal_encrypted_input(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="pw", pages=3)
    out = str(tmp_path / "out.pdf")
    journal = PageOpsJournal()
    journal.record(("delete", [1]))
    journal.record(("rotate", {0: 90}))
    apply_journal(enc, journal, out, password="pw")
    reader = PdfReader(out)
    assert len(reader.pages) == 2
    assert reader.pages[0].rotation % 360 == 90
    with pytest.raises(ValueError):
        apply_journal(enc, journal, out, password="nope")


# ---------------------------------------------------------------------------
# Randomized equivalence: images world vs. PDF world
# ---------------------------------------------------------------------------

AUX_SIZES = [(250, 330), (410, 260), (315, 475)]


def _generate_ops(rng, journal, model, aux_path):
    """Record 15 random valid ops, mirroring their effect on `model`.

    `model` entries are dicts {w, h, rot, pw, ph} (points, degrees, pixels).
    """
    kinds = ["delete", "move", "rotate", "insert_blank", "insert_pdf", "crop"]
    recorded = 0
    while recorded < 15:
        kind = rng.choice(kinds)
        n = len(model)
        if kind == "delete":
            if n < 4:
                continue
            count = rng.choice([1, 2])
            indices = rng.sample(range(n), count)
            journal.record(("delete", indices))
            for idx in sorted(indices, reverse=True):
                model.pop(idx)
        elif kind == "move":
            src, dst = rng.randrange(n), rng.randrange(n)
            journal.record(("move", src, dst))
            model.insert(dst, model.pop(src))
        elif kind == "rotate":
            idx = rng.randrange(n)
            degrees = rng.choice([90, 180, 270])
            journal.record(("rotate", {idx: degrees}))
            entry = model[idx]
            if degrees in (90, 270):
                entry["w"], entry["h"] = entry["h"], entry["w"]
                entry["pw"], entry["ph"] = entry["ph"], entry["pw"]
            entry["rot"] = (entry["rot"] + degrees) % 360
        elif kind == "insert_blank":
            idx = rng.randrange(n + 1)
            width_pt = rng.randrange(150, 400)
            height_pt = rng.randrange(150, 400)
            journal.record(("insert_blank", idx, [width_pt, height_pt]))
            model.insert(idx, {"w": width_pt, "h": height_pt, "rot": 0,
                               "pw": px(width_pt), "ph": px(height_pt)})
        elif kind == "insert_pdf":
            idx = rng.randrange(n + 1)
            indices = rng.sample(range(len(AUX_SIZES)), rng.choice([1, 2]))
            journal.record(("insert_pdf", idx, aux_path, indices))
            new_entries = [
                {"w": AUX_SIZES[i][0], "h": AUX_SIZES[i][1], "rot": 0,
                 "pw": px(AUX_SIZES[i][0]), "ph": px(AUX_SIZES[i][1])}
                for i in indices
            ]
            model[idx:idx] = new_entries
        else:  # crop
            idx = rng.randrange(n)
            entry = model[idx]
            if entry["pw"] < 140 or entry["ph"] < 140:
                continue
            x0 = rng.randrange(0, entry["pw"] - 120)
            x1 = rng.randrange(x0 + 100, entry["pw"] + 1)
            y0 = rng.randrange(0, entry["ph"] - 120)
            y1 = rng.randrange(y0 + 100, entry["ph"] + 1)
            journal.record(("crop", idx, [x0, y0, x1, y1]))
            entry["pw"], entry["ph"] = x1 - x0, y1 - y0
            entry["w"] = (x1 - x0) * PT_PER_PX
            entry["h"] = (y1 - y0) * PT_PER_PX
        recorded += 1


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_randomized_journal_equivalence(tmp_path, seed):
    rng = random.Random(seed)
    base_sizes = [(200 + 20 * i, 300 + 15 * i) for i in range(6)]
    src = make_sized_pdf(tmp_path / f"src_{seed}.pdf", base_sizes)
    aux = make_sized_pdf(tmp_path / f"aux_{seed}.pdf", AUX_SIZES)

    model = [{"w": w, "h": h, "rot": 0, "pw": px(w), "ph": px(h)}
             for w, h in base_sizes]
    journal = PageOpsJournal()
    _generate_ops(rng, journal, model, aux)

    # exercise persistence: replay from a JSON round trip
    journal = PageOpsJournal.from_dict(json.loads(json.dumps(journal.to_dict())))

    # world 1: fake image containers
    images = [FakePage(w, h) for w, h in base_sizes]
    callbacks = {
        "make_blank": lambda w, h: FakePage(w, h),
        "render_pdf_pages": lambda path, indices: [
            FakePage(*AUX_SIZES[i]) for i in indices],
    }
    journal.apply_to_images(images, callbacks)

    # world 2: the real PDF
    out = str(tmp_path / f"out_{seed}.pdf")
    apply_journal(src, journal, out)
    pages = PdfReader(out).pages

    assert len(pages) == len(model) == len(images)
    for i, (page, entry, fake) in enumerate(zip(pages, model, images)):
        # image world matches the model
        assert fake.image.size == (entry["pw"], entry["ph"]), f"page {i} px size"
        assert fake.width_in_pt == pytest.approx(entry["w"], abs=1e-6), f"page {i}"
        assert fake.height_in_pt == pytest.approx(entry["h"], abs=1e-6), f"page {i}"
        # PDF world matches the model (and therefore the image world)
        assert page.rotation % 360 == entry["rot"], f"page {i} rotation"
        eff_w, eff_h = effective_display_size(page)
        assert eff_w == pytest.approx(entry["w"], abs=2e-3), f"page {i} width"
        assert eff_h == pytest.approx(entry["h"], abs=2e-3), f"page {i} height"
