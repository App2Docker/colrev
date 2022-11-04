#!/usr/bin/env python3
import sys
from pathlib import Path

import colrev.review_manager


def main() -> int:
    print(sys.argv)
    msgfile = Path(sys.argv[1])

    review_manager = colrev.review_manager.ReviewManager()
    ret = review_manager.report(msg_file=msgfile)

    print(ret["msg"])

    return ret["status"]


if __name__ == "__main__":
    raise SystemExit(main())
