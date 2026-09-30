"""CLI:
    python -m hwsets book.pdf -o out.json [--pages 40-52]        real extraction (needs ANTHROPIC_API_KEY)
    python -m hwsets book.pdf --dry-run [--pages 40-52]          show what would be sent; NO API call, no key
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import ConfigurationError, describe_api_error, output_dir
from .pages import parse_page_spec


def _dry_run(args: argparse.Namespace) -> int:
    from .dryrun import BANNER, build_dry_run  # local import: keeps the real-extraction path unchanged

    print(BANNER, file=sys.stderr)
    report = build_dry_run(
        args.pdf, pages_hint=parse_page_spec(args.pages), chunk_size=args.chunk_size,
    )
    tag = f"_p{args.pages}" if args.pages else ""
    json_path = args.output or output_dir() / f"{args.pdf.stem}{tag}.dryrun.json"
    txt_path = json_path.with_suffix(".txt")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    txt_path.write_text(report.to_text(), encoding="utf-8")
    print(
        f"{len(report.candidate_pages)} candidate pages, {len(report.all_headers)} set headers, "
        f"{len(report.chunks)} chunks\n  json: {json_path}\n  text: {txt_path}\n{BANNER}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwsets", description="Extract hardware sets from a specbook PDF")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="write JSON here (default: stdout / outputs/ for --dry-run)")
    ap.add_argument("--pages", help="1-based pages to scan, e.g. 40-52,60 (skips auto-detection)")
    ap.add_argument("--dry-run", action="store_true", help="show exactly what would be sent; no API call")
    ap.add_argument("--chunk-size", type=int, default=4, help="pages per chunk (default 4, overlap 1)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    if args.dry_run:
        return _dry_run(args)

    import anthropic  # only the real-extraction path needs the SDK

    from .pipeline import run_pipeline

    try:
        result = run_pipeline(args.pdf, pages_hint=parse_page_spec(args.pages), chunk_size=args.chunk_size)
    except ConfigurationError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    except anthropic.APIError as e:
        print(describe_api_error(e), file=sys.stderr)
        return 3
    payload = result.model_dump_json(indent=2, exclude_none=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"{len(result.sets)} sets -> {args.output}", file=sys.stderr)
    else:
        print(payload)
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
