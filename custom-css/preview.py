#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Preview a custom stylesheet on the sample blog before you paste it into Ortszeit.

    uv run custom-css/preview.py custom-css/zeitung.css [--export EXPORT] [-o DIR] [--no-serve]

Copies the export (the shared sample-export by default), puts the stylesheet
into its blog.yaml as custom_css, and builds it with ../ssg/, whose pages
use the same markup and the same base sheet as the hosted blog. Then it
serves the result on http://localhost:8000/ until you stop it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SSG = REPO / "ssg" / "ortszeit_ssg.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview a custom stylesheet on an Ortszeit export.")
    parser.add_argument("css", type=Path, help="the stylesheet, e.g. custom-css/zeitung.css")
    parser.add_argument("--export", type=Path, default=REPO / "sample-export", help="an extracted export")
    parser.add_argument("-o", "--output", type=Path, help="where to build (default: a temporary folder)")
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("--no-serve", action="store_true", help="only build, do not start a server")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as workdir:
        export = Path(shutil.copytree(args.export, Path(workdir) / "blog"))
        blog = yaml.safe_load((export / "blog.yaml").read_text(encoding="utf-8"))
        blog["custom_css"] = args.css.read_text(encoding="utf-8")
        (export / "blog.yaml").write_text(yaml.safe_dump(blog, allow_unicode=True, sort_keys=False), encoding="utf-8")
        out = args.output or Path(workdir) / "site"
        command = ["uv", "run", str(SSG), str(export), "-o", str(out), "--lang", args.lang]
        if not args.no_serve:
            command.append("--serve")
        return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
