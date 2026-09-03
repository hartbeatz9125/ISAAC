from __future__ import annotations

import unittest

import numpy as np

from tts_once import LEADING_SILENCE_MS, add_leading_silence


class LeadingSilenceTests(unittest.TestCase):
    def test_exact_leading_silence_preserves_original_waveform(self):
        original = np.array([0.25, -0.5, 0.75], dtype=np.float32)
        sample_rate = 24_000
        result = add_leading_silence(original, sample_rate)
        prefix = round(sample_rate * LEADING_SILENCE_MS / 1000)
        self.assertEqual(LEADING_SILENCE_MS, 250)
        self.assertEqual(len(result), prefix + len(original))
        self.assertTrue(np.all(result[:prefix] == 0))
        np.testing.assert_array_equal(result[prefix:], original)


if __name__ == "__main__":
    unittest.main()
