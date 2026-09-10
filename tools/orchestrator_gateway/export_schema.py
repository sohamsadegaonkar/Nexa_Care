"""Export a draft or a schema configured for an operator-supplied HTTPS origin."""

import argparse
import json
from pathlib import Path

from gateway.api import openapi
from gateway.settings import validate_public_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", type=validate_public_url)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    doc = openapi(args.url)
    if args.url is None:
        doc["info"]["description"] += (
            " DRAFT: no deployed server has been configured; do not import until a real HTTPS origin is supplied."
        )
    args.output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
