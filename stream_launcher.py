"""MediaMTX chaqiradigan kirish nuqtasi — asosiy kod media/launcher.py da.

    python stream_launcher.py <slug>

Bu fayl ildizda turishi shart: mediamtx.yml dagi runOnDemand buyrug'i
aynan shu yo'lni ko'rsatadi.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from media.launcher import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
