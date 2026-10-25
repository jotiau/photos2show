"""Command line: photos2show-scan FOLDER [--parallel N] prints the analysis as a table."""
import argparse
import sys
from pathlib import Path

from photos2show import db
from photos2show.config import default_concurrency
from photos2show.pipeline import analyse_folder, describe_folder, skip_ai_reason


def main() -> None:
    parser = argparse.ArgumentParser(prog="photos2show-scan", description="Scan and describe a photo folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--parallel", type=int, choices=range(1, 17), metavar="N", default=default_concurrency(),
                        help="parallel AI requests, 1-16 (default: %(default)s, from AI_CONCURRENCY)")
    args = parser.parse_args()
    root = args.folder.expanduser()
    progress = lambda done, total: print(f"\r{done}/{total}", end="", file=sys.stderr)  # noqa: E731
    summary = analyse_folder(root, progress)
    print(" scanned", file=sys.stderr)
    described = describe_folder(root, progress, args.parallel)
    print(" described by AI", file=sys.stderr)
    summary["errors"] += described["errors"]
    conn = db.connect(root)
    print(f"{'id':>3}  {'file':<24} {'kind':<5} {'sharp':>7} {'bright':>6} {'contr':>6} {'edges':>6} {'entr':>5}  flags / AI")
    for it in db.all_media(conn):
        m = it["metrics"]
        skip = skip_ai_reason(it)
        print(f"{it['id']:>3}  {Path(it['path']).name:<24} {it['kind']:<5} {m['sharpness']:>7.1f} {m['brightness']:>6.2f} "
              f"{m['contrast']:>6.1f} {m['edge_density']:>6.3f} {m['entropy']:>5.2f}  "
              f"{','.join(it['flags']) or 'ok'}{f'  [no AI: {skip}]' if skip else ''}"
              f"{'  ' + ', '.join(it['ai'].get('tags') or []) if it['ai'] else ''}")
    for err in summary["errors"]:
        print(f"ERROR {err}", file=sys.stderr)
