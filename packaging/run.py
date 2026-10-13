import sys
from pathlib import Path

if __name__ == "__main__":
    if len(sys.argv) in (3, 5) and sys.argv[1] == "--self-test":
        from self_test import self_test
        runtime = Path(sys.argv[4]).resolve() if len(sys.argv) == 5 and sys.argv[3] == "--ocr-runtime" else None
        sys.exit(self_test(Path(sys.argv[2]).resolve(), runtime))
    else:
        from app.__main__ import main
        main()
