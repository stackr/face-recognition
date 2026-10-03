"""Public API benchmark; the legacy default remains one paced channel."""

import sys

from benchmark_multi import main

if __name__ == "__main__":
    if "--channels" not in sys.argv:
        sys.argv += ["--channels", "1"]
    if "--output" not in sys.argv:
        from api_session import ROOT

        sys.argv += ["--output", str(ROOT / "data/reports/benchmark.json")]
    main()
