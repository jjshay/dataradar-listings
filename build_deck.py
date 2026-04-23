"""Generate DATARADAR Strategic Overview deck."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

# Palette
BG = RGBColor(0x0B, 0x14, 0x20)
FG = RGBColor(0xF0, 0xF3, 0xF7)
BLUE = RGBColor(0x4A, 0x9E, 0xFF)
GREEN = RGBColor(0x30, 0xD1, 0x58)
GOLD = RGBColor(0xC8, 0xA3, 0x55)
MUTED = RGBColor(0x50, 0x5A, 0x6A)
DIM = RGBColor(0x7A, 0x84, 0x94)
PANEL = RGBColor(0x12, 0x1E, 0x2F)

FONT = "Calibri"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
SW, SH = prs.slide_width, prs.slide_height


def set_bg(slide, color=BG):
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
    bg.line.fill.background()
    bg.fill.solid()
    bg.fill.fore_color.rgb = color
    bg.shadow.inherit = False
    # send to back
    spTree = bg._element.getparent()
    spTree.remove(bg._element)
    spTree.insert(2, bg._element)
    return bg


def add_text(slide, left, top, width, height, text, size=18, bold=False,
             color=FG, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    tf.vertical_anchor = anchor
    lines = text.split("\n") if isinstance(text, str) else text
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
    return tb


def add_rich_lines(slide, left, top, width, height, lines, size=18,
                   color=FG, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
                   line_spacing=1.25, font=FONT):
    """lines: list of (text, bold, color_or_None, size_or_None)."""
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    tf.vertical_anchor = anchor
    for i, spec in enumerate(lines):
        text, bold, c, sz = spec
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        run = p.add_run()
        run.text = text
        run.font.name = font
        run.font.size = Pt(sz or size)
        run.font.bold = bold
        run.font.color.rgb = c or color
    return tb


def wordmark(slide):
    add_text(slide, Inches(11.7), Inches(7.15), Inches(1.5), Inches(0.3),
             "DATARADAR", size=9, color=MUTED, align=PP_ALIGN.RIGHT)


def accent_bar(slide, color=BLUE, top=None, left=None, w=None, h=None):
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        left or Inches(0.6), top or Inches(1.2),
        w or Inches(0.12), h or Inches(0.7))
    bar.line.fill.background()
    bar.fill.solid()
    bar.fill.fore_color.rgb = color
    return bar


def title(slide, text, accent=BLUE, y=Inches(0.55)):
    accent_bar(slide, color=accent, top=y, left=Inches(0.6),
               w=Inches(0.14), h=Inches(0.8))
    add_text(slide, Inches(0.9), y, Inches(12), Inches(0.9),
             text, size=36, bold=True, color=FG)


def new_slide():
    s = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(s)
    return s


def rounded_box(slide, left, top, w, h, fill=PANEL, line=None, radius=None):
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, w, h)
    box.fill.solid()
    box.fill.fore_color.rgb = fill
    if line is None:
        box.line.fill.background()
    else:
        box.line.color.rgb = line
        box.line.width = Pt(1)
    box.shadow.inherit = False
    # clear any default text
    box.text_frame.text = ""
    return box


# =========================================================================
# Slide 1 — Title
# =========================================================================
s = new_slide()
# Background geometric accents
big = s.shapes.add_shape(MSO_SHAPE.OVAL,
                         Inches(9.2), Inches(-2.5),
                         Inches(7.5), Inches(7.5))
big.line.fill.background()
big.fill.solid()
big.fill.fore_color.rgb = RGBColor(0x10, 0x2A, 0x4D)
big.shadow.inherit = False

# Accent diagonal bar
bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                         Inches(0), Inches(3.5),
                         Inches(5), Inches(0.08))
bar.line.fill.background()
bar.fill.solid()
bar.fill.fore_color.rgb = BLUE

# Gold dot
dot = s.shapes.add_shape(MSO_SHAPE.OVAL,
                         Inches(4.85), Inches(3.42),
                         Inches(0.24), Inches(0.24))
dot.line.fill.background()
dot.fill.solid()
dot.fill.fore_color.rgb = GOLD

add_text(s, Inches(0.8), Inches(2.2), Inches(10), Inches(1.4),
         "DATARADAR", size=84, bold=True, color=FG)
add_text(s, Inches(0.8), Inches(3.7), Inches(10), Inches(0.7),
         "Data-driven eBay inventory intelligence",
         size=24, color=BLUE)
add_text(s, Inches(0.8), Inches(4.55), Inches(10), Inches(0.5),
         "JJ Shay  ·  Gauntlet Gallery  ·  2026",
         size=18, color=FG)
add_text(s, Inches(0.8), Inches(6.4), Inches(10), Inches(0.4),
         "web-production-15df7.up.railway.app",
         size=14, color=GOLD)
wordmark(s)

# =========================================================================
# Slide 2 — The Problem
# =========================================================================
s = new_slide()
title(s, "eBay resellers are flying blind", accent=GOLD)

bullets = [
    ("•  54k+ comparable sales exist on the web — fragmented, unindexed", False, FG, 20),
    ("•  Sellers price by gut or by the single lowest competitor (race to bottom)", False, FG, 20),
    ("•  No systematic way to ask: \"what should this actually sell for?\"", False, FG, 20),
    ("•  Key-date events (artist deaths, anniversaries) move prices 5–35% — most sellers miss the window", False, FG, 20),
]
add_rich_lines(s, Inches(0.9), Inches(1.9), Inches(11.5), Inches(3.8),
               bullets, line_spacing=1.5)

# Bottom stat band
rounded_box(s, Inches(0.9), Inches(5.9), Inches(11.5), Inches(0.9),
            fill=RGBColor(0x15, 0x24, 0x3A))
add_text(s, Inches(0.9), Inches(5.9), Inches(11.5), Inches(0.9),
         "Price accuracy drives 60%+ of margin",
         size=22, bold=True, color=GOLD, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)
wordmark(s)

# =========================================================================
# Slide 3 — The Solution
# =========================================================================
s = new_slide()
title(s, "DATARADAR in one sentence", accent=BLUE)

add_text(s, Inches(0.9), Inches(2.1), Inches(11.5), Inches(1.6),
         "Every listing, priced by 54,000 comps\nand 4 AI models, in real time.",
         size=34, bold=True, color=FG, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)

# Flow diagram: 6 boxes
steps = ["eBay\nInventory", "54k Comp\nDB", "Smart\nAnchor",
         "4-LLM\nReview", "Match\nConsensus", "Re-price"]
colors = [BLUE, GOLD, BLUE, GREEN, GOLD, BLUE]
n = len(steps)
total_w = Inches(12.3)
box_w = Inches(1.75)
gap = (total_w - box_w * n) / (n - 1)
start_x = Inches(0.5)
row_y = Inches(5.1)
box_h = Inches(1.3)

centers = []
for i, (label, col) in enumerate(zip(steps, colors)):
    x = start_x + (box_w + gap) * i
    box = rounded_box(s, x, row_y, box_w, box_h,
                      fill=PANEL, line=col)
    tf = box.text_frame
    tf.margin_left = Emu(0); tf.margin_right = Emu(0)
    tf.margin_top = Emu(0); tf.margin_bottom = Emu(0)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = label
    run.font.name = FONT
    run.font.size = Pt(14)
    run.font.bold = True
    run.font.color.rgb = FG
    centers.append((x + box_w, row_y + box_h / 2))

# Arrows between boxes
for i in range(n - 1):
    x_end, y_mid = centers[i]
    next_x = start_x + (box_w + gap) * (i + 1)
    arrow = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                               x_end + Emu(20000),
                               y_mid - Inches(0.12),
                               next_x - x_end - Emu(40000),
                               Inches(0.24))
    arrow.line.fill.background()
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = BLUE
wordmark(s)

# =========================================================================
# Slide 4 — Data Moat
# =========================================================================
s = new_slide()
title(s, "The 54k-record comp database", accent=GOLD)

bullets = [
    ("•  54,000+ deduplicated past sales across Shepard Fairey, KAWS, Banksy, Death NYC, Mr. Brainwash, and more", False, FG, 18),
    ("•  Cleaned and indexed by artist, work_id, colorway, signed flag, medium, edition size", False, FG, 18),
    ("•  Weekly updates via scraper pipeline", False, FG, 18),
    ("•  Powers /prices mobile UI for on-the-go lookup", False, FG, 18),
]
add_rich_lines(s, Inches(0.9), Inches(1.9), Inches(7.5), Inches(4),
               bullets, line_spacing=1.6)

# Stat panel on right
rounded_box(s, Inches(9), Inches(1.9), Inches(3.7), Inches(4),
            fill=PANEL, line=GOLD)
add_text(s, Inches(9), Inches(2.1), Inches(3.7), Inches(1.2),
         "54,000+", size=64, bold=True, color=GOLD,
         align=PP_ALIGN.CENTER)
add_text(s, Inches(9), Inches(3.3), Inches(3.7), Inches(0.5),
         "comps in DB", size=14, color=DIM,
         align=PP_ALIGN.CENTER)
add_text(s, Inches(9), Inches(4.1), Inches(3.7), Inches(0.9),
         "<200ms", size=44, bold=True, color=BLUE,
         align=PP_ALIGN.CENTER)
add_text(s, Inches(9), Inches(5.0), Inches(3.7), Inches(0.5),
         "median search latency", size=14, color=DIM,
         align=PP_ALIGN.CENTER)
wordmark(s)

# =========================================================================
# Slide 5 — Smart Pricing Engine
# =========================================================================
s = new_slide()
title(s, "From gut feel to comp-grounded math", accent=BLUE)

# Two columns
col_w = Inches(5.8)
col_h = Inches(2.6)
col_y = Inches(1.9)

# LEFT (old, dim)
rounded_box(s, Inches(0.9), col_y, col_w, col_h,
            fill=RGBColor(0x15, 0x1D, 0x2A), line=DIM)
add_text(s, Inches(1.1), col_y + Inches(0.3), col_w, Inches(0.4),
         "BEFORE", size=13, bold=True, color=DIM)
add_text(s, Inches(1.1), col_y + Inches(0.85), col_w - Inches(0.4), Inches(1.5),
         "base_price  ×  event_boost",
         size=22, bold=True, color=DIM, font="Consolas")

# RIGHT (new, accent)
rounded_box(s, Inches(6.95), col_y, col_w, col_h,
            fill=PANEL, line=GREEN)
add_text(s, Inches(7.15), col_y + Inches(0.3), col_w, Inches(0.4),
         "NOW", size=13, bold=True, color=GREEN)
add_text(s, Inches(7.15), col_y + Inches(0.85), col_w - Inches(0.4), Inches(1.5),
         "comp_p75  ×  event_multiplier\nwhen ≥3 comps",
         size=22, bold=True, color=FG, font="Consolas")

# Signed-gate note
add_text(s, Inches(0.9), Inches(4.9), Inches(11.5), Inches(1.1),
         "Signed-gate: auto-filters comps to signed-only when title qualifies. "
         "Falls back to full pool if <3 signed comps exist.",
         size=16, color=FG, align=PP_ALIGN.CENTER)

# Code snippet
rounded_box(s, Inches(2.5), Inches(6.2), Inches(8.3), Inches(0.8),
            fill=RGBColor(0x06, 0x0C, 0x14), line=BLUE)
add_text(s, Inches(2.5), Inches(6.2), Inches(8.3), Inches(0.8),
         "suggested = comp_p75 × (1 + event_boost_pct / 100)",
         size=15, color=GREEN, font="Consolas",
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
wordmark(s)

# =========================================================================
# Slide 6 — 4-LLM Consensus Review
# =========================================================================
s = new_slide()
title(s, "Four models. One price.", accent=GREEN)

# 2x2 grid
models = [
    ("CLAUDE", "Anthropic  ·  claude-sonnet-4-6", BLUE),
    ("GPT-4o", "OpenAI", GREEN),
    ("GEMINI 2.0 FLASH", "Google", GOLD),
    ("GROK 3", "xAI", BLUE),
]
grid_x0 = Inches(0.9)
grid_y0 = Inches(1.7)
cell_w = Inches(5.8)
cell_h = Inches(1.25)
gap_x = Inches(0.3)
gap_y = Inches(0.2)

for i, (name, sub, col) in enumerate(models):
    r, c = divmod(i, 2)
    x = grid_x0 + (cell_w + gap_x) * c
    y = grid_y0 + (cell_h + gap_y) * r
    rounded_box(s, x, y, cell_w, cell_h, fill=PANEL, line=col)
    add_text(s, x + Inches(0.3), y + Inches(0.2), cell_w - Inches(0.6), Inches(0.55),
             name, size=22, bold=True, color=FG)
    add_text(s, x + Inches(0.3), y + Inches(0.75), cell_w - Inches(0.6), Inches(0.4),
             sub, size=12, color=DIM)

# Body bullets
bullets = [
    ("•  Same prompt to each: title, artist, your price, comp stats, recent sales, supply count", False, FG, 14),
    ("•  Fans out in parallel (5–10s wall clock)", False, FG, 14),
    ("•  Cached per (listing_id, comp_median bucket) — invalidates only when comp_median shifts ≥$10", False, FG, 14),
    ("•  Consensus = median of valid prices", False, FG, 14),
]
add_rich_lines(s, Inches(0.9), Inches(4.9), Inches(12), Inches(1.8),
               bullets, line_spacing=1.35)

# Bottom stat
add_text(s, Inches(0.9), Inches(6.75), Inches(11.5), Inches(0.4),
         "~$0.01–0.02 per item  ·  cached until comps move",
         size=15, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
wordmark(s)

# =========================================================================
# Slide 7 — Swipe Mode UX
# =========================================================================
s = new_slide()
title(s, "Tinder for inventory", accent=BLUE)

# Left: body text
bullets = [
    ("•  Full-screen card view, one listing at a time", False, FG, 18),
    ("•  Two tabs per card: Comps (histogram + year trend + recent sales) · LLM Consensus (4 model cards + Match button)", False, FG, 18),
    ("•  Touch swipe, keyboard arrows, Esc to close", False, FG, 18),
    ("•  \"Match Consensus\" auto-pushes the price to eBay in one click", False, FG, 18),
]
add_rich_lines(s, Inches(0.9), Inches(1.9), Inches(7.4), Inches(4.8),
               bullets, line_spacing=1.55)

# Right: card sketch — 3 stacked labeled rectangles
card_x = Inches(9.0)
card_y = Inches(1.8)
card_w = Inches(3.6)
# Outer card frame
rounded_box(s, card_x, card_y, card_w, Inches(5.0),
            fill=PANEL, line=BLUE)
# Header
rounded_box(s, card_x + Inches(0.2), card_y + Inches(0.25),
            card_w - Inches(0.4), Inches(1.1),
            fill=RGBColor(0x1C, 0x2B, 0x42))
add_text(s, card_x + Inches(0.2), card_y + Inches(0.25),
         card_w - Inches(0.4), Inches(1.1),
         "HEADER\nTitle · Artist · Price",
         size=12, bold=True, color=FG, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)
# Tabs
rounded_box(s, card_x + Inches(0.2), card_y + Inches(1.55),
            card_w - Inches(0.4), Inches(2.3),
            fill=RGBColor(0x1C, 0x2B, 0x42))
add_text(s, card_x + Inches(0.2), card_y + Inches(1.55),
         card_w - Inches(0.4), Inches(2.3),
         "TABS\nComps · LLM Consensus",
         size=12, bold=True, color=GOLD, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)
# Footer
rounded_box(s, card_x + Inches(0.2), card_y + Inches(4.05),
            card_w - Inches(0.4), Inches(0.7),
            fill=RGBColor(0x14, 0x38, 0x24))
add_text(s, card_x + Inches(0.2), card_y + Inches(4.05),
         card_w - Inches(0.4), Inches(0.7),
         "FOOTER · Match Consensus",
         size=12, bold=True, color=GREEN, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)
wordmark(s)

# =========================================================================
# Slide 8 — Architecture
# =========================================================================
s = new_slide()
title(s, "Under the hood", accent=GOLD)

# Center Flask box
center_w = Inches(3.2)
center_h = Inches(1.5)
cx = (SW - center_w) / 2
cy = Inches(3.4)
rounded_box(s, cx, cy, center_w, center_h, fill=PANEL, line=BLUE)
add_text(s, cx, cy, center_w, center_h,
         "Flask app\napp.py (14k lines)",
         size=16, bold=True, color=FG, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)

# Inputs (left side)
in_specs = [
    ("eBay Trading API", Inches(0.5), Inches(1.7)),
    ("Google Sheets\n(pricing rules)", Inches(0.5), Inches(3.3)),
    ("54k comp DB\n(data/*.json)", Inches(0.5), Inches(4.9)),
]
for label, x, y in in_specs:
    rounded_box(s, x, y, Inches(2.5), Inches(1.1), fill=PANEL, line=GOLD)
    add_text(s, x, y, Inches(2.5), Inches(1.1),
             label, size=13, bold=True, color=FG, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)
    # Arrow to center
    arrow = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                               x + Inches(2.55), y + Inches(0.45),
                               cx - (x + Inches(2.55)) - Emu(30000),
                               Inches(0.22))
    arrow.line.fill.background()
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = GOLD

# Outputs (right side) — 4 LLM APIs
out_specs = [
    ("Claude API", Inches(10.3), Inches(1.55)),
    ("OpenAI API", Inches(10.3), Inches(2.85)),
    ("Gemini API", Inches(10.3), Inches(4.15)),
    ("Grok API", Inches(10.3), Inches(5.45)),
]
for label, x, y in out_specs:
    rounded_box(s, x, y, Inches(2.5), Inches(0.9), fill=PANEL, line=GREEN)
    add_text(s, x, y, Inches(2.5), Inches(0.9),
             label, size=13, bold=True, color=FG, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)
    arrow = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                               cx + center_w + Emu(30000),
                               y + Inches(0.35),
                               x - (cx + center_w) - Emu(60000),
                               Inches(0.22))
    arrow.line.fill.background()
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = GREEN

# Footer notes
add_text(s, Inches(0.9), Inches(6.7), Inches(11.5), Inches(0.4),
         "Deployment: Railway (Procfile → python app.py)  ·  "
         "Persistence: JSON files + Google Sheets",
         size=13, color=DIM, align=PP_ALIGN.CENTER)
wordmark(s)

# =========================================================================
# Slide 9 — Traction snapshot
# =========================================================================
s = new_slide()
title(s, "Where we are", accent=GREEN)

stats = [
    ("54,000+", "records in comp DB", GOLD),
    ("~200", "active eBay listings managed", BLUE),
    ("2 of 4", "LLMs live\nClaude + Grok · OpenAI/Gemini pending billing", GREEN),
    ("<2s", "wall clock, 4-LLM fan-out", BLUE),
]
card_w = Inches(2.85)
card_h = Inches(3.0)
total = card_w * 4 + Inches(0.3) * 3
start = (SW - total) / 2
cy = Inches(2.2)
for i, (big, label, col) in enumerate(stats):
    x = start + (card_w + Inches(0.3)) * i
    rounded_box(s, x, cy, card_w, card_h, fill=PANEL, line=col)
    add_text(s, x, cy + Inches(0.35), card_w, Inches(1.5),
             big, size=54, bold=True, color=col, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)
    add_text(s, x + Inches(0.2), cy + Inches(1.95), card_w - Inches(0.4), Inches(1.0),
             label, size=14, color=FG, align=PP_ALIGN.CENTER)

add_text(s, Inches(0.9), Inches(5.8), Inches(11.5), Inches(0.5),
         "Deployed live at web-production-15df7.up.railway.app",
         size=16, bold=True, color=GOLD, align=PP_ALIGN.CENTER)
add_text(s, Inches(0.9), Inches(6.3), Inches(11.5), Inches(0.4),
         "Railway auto-deploy from GitHub main",
         size=13, color=DIM, align=PP_ALIGN.CENTER)
wordmark(s)

# =========================================================================
# Slide 10 — Roadmap
# =========================================================================
s = new_slide()
title(s, "What's next", accent=BLUE)

cols = [
    ("NOW", "Next 2 weeks", GREEN, [
        "OpenAI + Gemini quota top-up",
        "Bulk batch repricing approval UI",
        "Nightly comp re-index",
    ]),
    ("NEXT", "Q3 2026", BLUE, [
        "More data sources\n(Heritage, Christie's)",
        "Artist-specific LLM fine-tuning",
        "Mobile PWA",
    ]),
    ("LATER", "Horizon", GOLD, [
        "Open comp DB as paid API",
        "Multi-marketplace\n(Mercari, Poshmark)",
        "AI-authenticated COA verification",
    ]),
]
col_w = Inches(3.9)
col_h = Inches(4.9)
total = col_w * 3 + Inches(0.3) * 2
start = (SW - total) / 2
cy = Inches(1.9)
for i, (hdr, sub, col, items) in enumerate(cols):
    x = start + (col_w + Inches(0.3)) * i
    rounded_box(s, x, cy, col_w, col_h, fill=PANEL, line=col)
    add_text(s, x + Inches(0.3), cy + Inches(0.25), col_w - Inches(0.6), Inches(0.6),
             hdr, size=24, bold=True, color=col)
    add_text(s, x + Inches(0.3), cy + Inches(0.85), col_w - Inches(0.6), Inches(0.4),
             sub, size=12, color=DIM)
    # items
    lines = [("•  " + item, False, FG, 14) for item in items]
    add_rich_lines(s, x + Inches(0.3), cy + Inches(1.45),
                   col_w - Inches(0.6), col_h - Inches(1.6),
                   lines, line_spacing=1.4)
wordmark(s)

# =========================================================================
# Slide 11 — Strategic Options
# =========================================================================
s = new_slide()
title(s, "Why this matters", accent=GOLD)

opts = [
    ("Own it", GREEN,
     "Gauntlet Gallery owns a pricing stack worth $50k+ in engineering with $0 ongoing (free-tier APIs fit this scale)."),
    ("License it", BLUE,
     "Other art and collectibles resellers pay $50–200/mo for access."),
    ("Sell it", GOLD,
     "A marketplace (Heritage, StockX, 1stDibs) acquires the comp DB + engine as an upgrade to their seller tools."),
]
row_w = Inches(12.0)
row_h = Inches(1.3)
row_x = (SW - row_w) / 2
for i, (name, col, body) in enumerate(opts):
    y = Inches(1.85) + (row_h + Inches(0.25)) * i
    rounded_box(s, row_x, y, row_w, row_h, fill=PANEL, line=col)
    # Left label
    add_text(s, row_x + Inches(0.4), y, Inches(2.6), row_h,
             name, size=22, bold=True, color=col,
             anchor=MSO_ANCHOR.MIDDLE)
    # Separator bar
    sep = s.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                             row_x + Inches(3.05), y + Inches(0.25),
                             Inches(0.03), row_h - Inches(0.5))
    sep.line.fill.background()
    sep.fill.solid()
    sep.fill.fore_color.rgb = col
    # Body
    add_text(s, row_x + Inches(3.3), y, row_w - Inches(3.6), row_h,
             body, size=15, color=FG,
             anchor=MSO_ANCHOR.MIDDLE)

add_text(s, Inches(0.9), Inches(6.6), Inches(11.5), Inches(0.45),
         "TAM for collectibles software: ~$400M · growing 12% YoY",
         size=13, color=DIM, align=PP_ALIGN.CENTER)
wordmark(s)

# =========================================================================
# Slide 12 — Contact / End card
# =========================================================================
s = new_slide()
# Decorative accent
bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                         Inches(5.5), Inches(1.5),
                         Inches(2.3), Inches(0.08))
bar.line.fill.background()
bar.fill.solid()
bar.fill.fore_color.rgb = BLUE

add_text(s, Inches(0.9), Inches(1.8), Inches(11.5), Inches(0.9),
         "JJ Shay  ·  Gauntlet Gallery",
         size=40, bold=True, color=FG, align=PP_ALIGN.CENTER)

contact_lines = [
    ("jjshay@gmail.com", False, FG, 20),
    ("linkedin.com/in/jjshay", False, BLUE, 20),
    ("github.com/jjshay/dataradar-listings", False, GOLD, 20),
]
add_rich_lines(s, Inches(0.9), Inches(3.3), Inches(11.5), Inches(2.5),
               contact_lines, align=PP_ALIGN.CENTER, line_spacing=1.8)

add_text(s, Inches(0.9), Inches(6.1), Inches(11.5), Inches(0.6),
         "Thanks for your time.",
         size=18, color=DIM, align=PP_ALIGN.CENTER)
wordmark(s)

# Save
out = "/tmp/dataradar-listings/DATARADAR_Strategic_Overview.pptx"
prs.save(out)
print(f"Saved: {out}")
print(f"Slides: {len(prs.slides)}")
