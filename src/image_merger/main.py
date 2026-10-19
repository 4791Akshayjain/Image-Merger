import argparse
import sys

from image_merger.format import ImageMergerError
from image_merger.image_pipeline import create_merged_png, extract_image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hide two code-protected images inside one viewable PNG."
    )
    sub = parser.add_subparsers(dest="command")

    m = sub.add_parser("merge", help="Image A + Image B + carrier -> merged PNG")
    m.add_argument("--image-a", required=True)
    m.add_argument("--code-a", required=True, help="3-20 digits")
    m.add_argument("--image-b", required=True)
    m.add_argument("--code-b", required=True, help="3-20 digits, different from code A")
    m.add_argument("--carrier", required=True, help="Image that is visible in the merged PNG")
    m.add_argument("--output", required=True, help="Path of the merged .png to write")

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
            create_merged_png(args.image_a, args.code_a, args.image_b, args.code_b,
                              args.carrier, args.output)
        else:
            result = extract_image(args.input, args.code)
            result.image.save(args.output, format="PNG")
            print(f"[OK] '{args.input}' -> '{args.output}' "
                  f"({result.image.width}x{result.image.height})")
    except ImageMergerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
