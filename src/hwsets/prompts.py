SYSTEM_PROMPT = """\
You extract DOOR HARDWARE SETS from pages of a construction specification (Division 08 Openings, \
section 08 71 00 Door Hardware). Input is text lines from a PDF, each prefixed with an id like [12-7]. \
Where a line has a large horizontal gap between cells it is rendered as " | ", which exposes table columns.

## What to extract
A hardware set is a named group of components (hinges, locksets, closers, exit devices, kick plates, \
thresholds, gasketing ...) assigned to doors. Formats:
- List format: a header such as "SET #1 - ENTRANCE DOORS" / "HARDWARE SET 3A" followed by component lines.
- Table format: rows are components, grouped under set headers or set-number cells.
Extract EVERY set you can see, in document order. Ignore door schedules, general notes, product spec \
paragraphs and anything that is not a set with components (a door-to-set assignment list is NOT a set).

## Step 1: layout (fill this first)
Before extracting sets, decide the column layout of the component data and record it in `layout`. \
Assign each column a role: qty, description, catalog_number, mfr, finish, notes, other. \
Base the role on the header AND on the values in the whole column, not on a single cell.

## Manufacturer vs finish (most important rule)
Short codes are used for both. Manufacturer codes: IV/IVES, HAG, MK, LCN, SCH, VON, PE/PEMKO, NO/NORTON, \
RO, ZER, ... Finish codes: US26D, US32D, US3, 626, 630, 652, 689, BSP, BLK, ANOD, PE (painted enamel), ... \
Some codes are ambiguous: "PE" = Pemko (mfr) OR painted enamel (finish); "NO" = Norton OR the word "No". \
Resolve ambiguity from the COLUMN context: a column of mostly MK, LCN, SCH, IVES is the mfr column; a column \
of US26D, 630, BSP is the finish column; whatever sits in that column takes that role, including an \
ambiguous code. Never decide from one value alone. When the header is missing, use the column's other values \
and position. If the source has only one of the two columns, leave the other null. \
A finish or mfr may also be printed inline in the description text (e.g. "... US26D"); extract it only \
when it is clearly a separate token in a separate cell or clearly trailing; otherwise leave it in the description.

## Sets
- set_number: identifier only ("SET #1" -> "1", "HW-3A" -> "3A", "Hardware Set No. 15" -> "15").
- description: the heading text after the number ("ENTRANCE DOORS"), else null.
- Boundaries: a new set starts at a new set header or a change of set number, even when it is only a blank \
row or a small label. Do not merge two sets; do not split one set at a page break.
- NOT USED / N/A / SPARE / "DELETED" sets: STILL emit them, status="not_used", with whatever components are \
listed (possibly none).
- Multi-page: a set may run across page breaks (page headers/footers/repeated column headings in the middle \
of it are not part of it). Set continues_from_previous_page=true when the first page shown begins in the \
middle of a set whose header is not shown (use the set number from a "continued" line if present, otherwise \
best-guess from context and lower confidence), and continues_on_next_page=true when the last page ends \
mid-set.

## Components
- qty: integer exactly as printed. If there is no quantity, use null. NEVER guess or default to 1. \
Quantity words like "EA", "PR" are units, not the quantity: "1 PR" -> 1 (note the unit in notes); "PR" alone -> null.
- description: the item name/details as printed (fix only obvious line-wrapping).
- catalog_number: the model/catalog/part number token(s), else null.
- notes: remarks, footnote markers, "by others", option codes, and other free text that fits no other field.
- Text that wraps onto a second line belongs to the same component; join it.
- Copy text exactly. Do not translate codes into full names, do not correct spelling, do not invent values.
- source_line_ids: every line id the component's text came from (used for locating it on the page).
- confidence: 0-1. Use below 0.6 when mfr/finish assignment, quantity or boundaries are uncertain.
- header_line_ids: the line id(s) of the set header.

Return only the structured result."""


def continuation_block(context) -> str | None:
    """Short deterministic note for a chunk that starts inside an already-open set; None when not needed.

    Guidance only - built solely from the header ids/lines the detector found, never from source content.
    """
    if context is None or not context.is_continuation or not context.open_set_id:
        return None
    lines = [
        "CONTINUATION CONTEXT (guidance only; the source lines below remain authoritative):",
        f"This chunk begins inside hardware set {context.open_set_id!r}. "
        f"Its header is at source line [{context.header_line_id}] on page {context.header_page} "
        "and is not part of this message.",
    ]
    if context.continues_until_line_id:
        lines.append(
            f"The lines at the start of this chunk, before the next set header at [{context.continues_until_line_id}], "
            f"belong to set {context.open_set_id!r}."
        )
    else:
        lines.append(
            f"All lines in this chunk belong to set {context.open_set_id!r} until another set header appears."
        )
    if context.open_set_not_used:
        lines.append(
            "The open set is marked NOT USED/N/A. Preserve the set (status not_used) even if it contains no "
            "components; do not invent components for it."
        )
    lines.append(
        "Do not add quantities, components, manufacturers, finishes, catalog numbers or descriptions "
        "that are not printed in the source lines."
    )
    return "\n".join(lines)


def build_user_message(pages, context=None) -> str:
    """The exact user message sent for a chunk. Shared by the real extractor and the dry run.

    With no continuation the message is exactly the plain page text; otherwise the short continuation block
    is prepended and everything after it is unchanged.
    """
    from .ingest import format_lines

    base = (
        f"Pages {pages[0].number}-{pages[-1].number} of the specification follow. "
        "Extract all hardware sets.\n\n" + format_lines(pages)
    )
    block = continuation_block(context)
    return base if block is None else block + "\n\n" + base
