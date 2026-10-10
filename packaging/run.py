import sys
from pathlib import Path

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from self_test import self_test
        sys.exit(self_test(Path(sys.argv[2]).resolve()))
    else:
        from app.__main__ import main
        main()
