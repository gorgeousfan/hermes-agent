"""Regression tests for hardware_check meminfo guard."""

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestHardwareCheckMeminfoGuard(unittest.TestCase):
    """hardware_check must guard against malformed /proc/meminfo lines."""

    def test_memtotal_split_guard_in_source(self):
        """Source must contain a length check before indexing line.split()[1]."""
        source = (REPO_ROOT / "skills" / "creative" / "comfyui" / "scripts" / "hardware_check.py").read_text()
        self.assertIn("parts = line.split()", source)
        self.assertIn("len(parts) >= 2", source)


if __name__ == "__main__":
    unittest.main()
