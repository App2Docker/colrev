#!/usr/bin/env python3
import colrev.review_manager

PASS = 0
FAIL = 1


def main() -> int:

    review_manager = colrev.review_manager.ReviewManager()
    ret = review_manager.format_records_file()

    print(ret["msg"])

    return ret["status"]


if __name__ == "__main__":
    raise SystemExit(main())
