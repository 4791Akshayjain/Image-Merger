import argparse
import sys

from image_merger.format import ImageMergerError
from image_merger.image_pipeline import DEFAULT_QUALITY, create_merged_png, extract_image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hide two code-protected images inside one viewable PNG."
    )
    sub = parser.add_subparsers(dest="command")

    m = sub.add_parser("merge", help="One or two images + carrier -> merged PNG")
    m.add_argument("--image-a", required=True)
    m.add_argument("--code-a", required=True, help="3-20 digits")
    m.add_argument("--image-b", help="Optional second image")
    m.add_argument("--code-b", help="3-20 digits, different from code A; required with --image-b")
    m.add_argument("--carrier", required=True, help="Image that is visible in the merged PNG")
    m.add_argument("--output", required=True, help="Path of the merged .png to write")
    m.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                   help="Hidden-image quality 1-99, or 100 for lossless (default %(default)s)")
    m.add_argument("--no-auto-fit", action="store_true",
                   help="Do not lower the quality to keep the carrier lightly filled")
    m.add_argument("--max-mp", type=float, help="Scale the carrier down to at most this many megapixels")

    e = sub.add_parser("extract", help="Merged PNG + code -> image (.png)")
    e.add_argument("input", help="Merged PNG")
    e.add_argument("output", help="Path of the recovered .png to write")
    e.add_argument("--code", required=True)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command is None:
        from image_merger.gui import main as gui_main  # lazy: CLI needs no Tkinter
        gui_main()
        return 0

    try:
        if args.command == "merge":
            report = create_merged_png(args.image_a, args.code_a, args.image_b, args.code_b,
                                       args.carrier, args.output, quality=args.quality,
                                       auto_fit=not args.no_auto_fit, max_carrier_mp=args.max_mp)
            print(f"[OK] merged -> '{report.path}' ({report.size_bytes / (1024 * 1024):.1f} MB, "
                  f"carrier fill {report.fill:.0%} = {report.level} detection risk, "
                  f"hidden quality {report.quality})")
        else:
            result = extract_image(args.input, args.code)
            result.image.save(args.output, format="PNG")
            print(f"[OK] '{args.input}' -> '{args.output}' "
                  f"({result.image.width}x{result.image.height})")
            if result.matched_index is None:
                print("Note: the code did not match a hidden image (or the file has none); "
                      "the output is a noise image.", file=sys.stderr)
    except ImageMergerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
