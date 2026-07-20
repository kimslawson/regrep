"""Command-line interface.

Exit codes follow grep: 0 = at least one match, 1 = no matches, 2 = error.
"""

import argparse
import os
import re
import sys
import time

from . import __version__
from .core import SearchOptions, run_search
from .output import (
    make_palette,
    print_json,
    print_results,
    print_timeline,
    print_timeline_json,
    summary_line,
)
from .providers import DEFAULT_PROVIDER, PROVIDERS, ProviderError, get_provider
from .timeline import build_timeline

MAX_WORKERS = 32  # hard ceiling; archives are a shared resource


def timestamp_arg(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", value)
    if not 4 <= len(digits) <= 14 or len(digits) % 2:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r} (use YYYY, YYYY-MM, YYYY-MM-DD, "
            "or up to a full YYYYMMDDhhmmss timestamp)"
        )
    return digits


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regrep",
        description="grep the history of the web: regex search across archived "
        "snapshots of a URL.",
        epilog="exit codes: 0 = matches found, 1 = no matches, 2 = error",
    )
    parser.add_argument("pattern", help="regular expression to search for")
    parser.add_argument("url", help="URL whose archived history to search")

    matching = parser.add_argument_group("matching")
    matching.add_argument(
        "-i", "--ignore-case", action="store_true", help="case-insensitive matching"
    )
    matching.add_argument(
        "-F",
        "--fixed-strings",
        action="store_true",
        help="treat PATTERN as a literal string, not a regex",
    )
    matching.add_argument(
        "--visible",
        action="store_true",
        help="search only human-visible page text (strips tags, scripts, styles, "
        "comments); line numbers then refer to the extracted text",
    )

    selection = parser.add_argument_group("snapshot selection")
    selection.add_argument(
        "--from",
        dest="from_ts",
        type=timestamp_arg,
        metavar="DATE",
        help="earliest snapshot to consider (e.g. 2023, 2023-06, 2023-06-15)",
    )
    selection.add_argument(
        "--to",
        dest="to_ts",
        type=timestamp_arg,
        metavar="DATE",
        help="latest snapshot to consider",
    )
    selection.add_argument(
        "-l",
        "--limit",
        type=nonnegative_int,
        default=100,
        help="max snapshots to consider, 0 for no limit (default: 100)",
    )
    cadence = selection.add_mutually_exclusive_group()
    cadence.add_argument(
        "--daily",
        action="store_const",
        const=8,
        dest="collapse",
        help="at most one snapshot per day",
    )
    cadence.add_argument(
        "--monthly",
        action="store_const",
        const=6,
        dest="collapse",
        help="at most one snapshot per month",
    )
    cadence.add_argument(
        "--yearly",
        action="store_const",
        const=4,
        dest="collapse",
        help="at most one snapshot per year",
    )
    selection.add_argument(
        "--prefix",
        action="store_true",
        help="treat URL as a prefix and search everything archived under it",
    )
    selection.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        default=DEFAULT_PROVIDER,
        help="archive to search (default: %(default)s)",
    )

    transport = parser.add_argument_group("fetching")
    transport.add_argument(
        "-w",
        "--workers",
        type=positive_int,
        default=5,
        help="concurrent fetches (default: 5; be kind to the archive)",
    )
    transport.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="per-snapshot read timeout (default: 30)",
    )
    transport.add_argument(
        "--max-size",
        type=positive_int,
        default=10,
        metavar="MB",
        help="skip snapshots larger than this (default: 10)",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--timeline",
        action="store_true",
        help="instead of listing every match, report when the pattern appeared "
        "and vanished across snapshots (keeps duplicate captures so a "
        "reappearance is detected); exit 0 if it was ever present",
    )
    output.add_argument(
        "--max-columns",
        type=nonnegative_int,
        default=200,
        metavar="N",
        help="truncate displayed lines to a window of N characters around the "
        "match, 0 to never truncate (default: 200)",
    )
    output.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="when to use terminal colors (default: auto)",
    )
    output.add_argument(
        "--json",
        action="store_true",
        help="emit one JSON object per matching line (NDJSON) instead of "
        "human-readable output",
    )
    output.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress progress and summary messages on stderr",
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    pattern = re.escape(args.pattern) if args.fixed_strings else args.pattern
    try:
        regex = re.compile(pattern, re.IGNORECASE if args.ignore_case else 0)
    except re.error as exc:
        print(f"regrep: invalid regex: {exc}", file=sys.stderr)
        return 2

    provider = get_provider(args.provider)
    options = SearchOptions(
        visible=args.visible,
        workers=min(args.workers, MAX_WORKERS),
        timeout=args.timeout,
        max_bytes=args.max_size * 1024 * 1024,
        from_ts=args.from_ts,
        to_ts=args.to_ts,
        limit=args.limit or None,
        prefix=args.prefix,
        collapse=args.collapse,
        # Timeline needs to see a phrase come back, which shows up as a
        # repeated content digest; deduping those would hide the reappearance.
        dedupe=not args.timeline,
    )

    chatty = not args.quiet

    def note(message: str) -> None:
        if chatty:
            print(message, file=sys.stderr)

    live_progress = chatty and sys.stderr.isatty()

    def progress(done: int, total: int) -> None:
        if live_progress:
            print(f"\r[regrep] scanned {done}/{total} ", end="", file=sys.stderr, flush=True)

    note(f"[regrep] {args.provider}: listing snapshots of {args.url} ...")
    started = time.monotonic()
    try:
        outcome = run_search(provider, args.url, regex, options, progress=progress)
    except ProviderError as exc:
        print(f"regrep: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nregrep: interrupted", file=sys.stderr)
        return 130
    if live_progress and outcome.stats.scanned:
        print("\r\033[K", end="", file=sys.stderr, flush=True)

    try:
        if args.timeline:
            segments = build_timeline(outcome.presence)
            if args.json:
                print_timeline_json(segments)
            else:
                palette = make_palette(args.color, sys.stdout)
                print_timeline(segments, palette, max_columns=args.max_columns)
        elif args.json:
            print_json(outcome)
        else:
            palette = make_palette(args.color, sys.stdout)
            print_results(outcome, palette, max_columns=args.max_columns)
        sys.stdout.flush()
    except BrokenPipeError:
        # Downstream (e.g. `| head`) closed the pipe; exit the way grep does.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141

    if outcome.stats.listed == 0:
        note(f"[regrep] no snapshots found for {args.url}")
        return 1
    note(summary_line(outcome.stats, time.monotonic() - started))
    for sample in outcome.stats.error_samples:
        note(f"[regrep]   fetch error: {sample}")
    return 0 if outcome.results else 1


if __name__ == "__main__":
    sys.exit(main())
