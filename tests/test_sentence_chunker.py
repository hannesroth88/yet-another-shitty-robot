"""Unit tests for the streaming sentence chunker.

Run:  .venv/bin/python -m unittest tests.test_sentence_chunker -v
"""
from __future__ import annotations

import unittest

from src.text.sentence_chunker import SentenceChunker


def _feed(chunker: SentenceChunker, text: str, step: int = 3) -> list[str]:
    """Feed text in small slices to simulate token streaming."""
    out: list[str] = []
    for i in range(0, len(text), step):
        out.extend(chunker.push(text[i : i + step]))
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


class SentenceChunkerTest(unittest.TestCase):
    def test_basic_sentences(self) -> None:
        c = SentenceChunker()
        self.assertEqual(
            _feed(c, "Hallo Welt. Wie geht es dir? Gut!"),
            ["Hallo Welt.", "Wie geht es dir?", "Gut!"],
        )

    def test_streaming_word_by_word(self) -> None:
        c = SentenceChunker()
        out: list[str] = []
        for tok in ["Die ", "Haupt", "stadt ", "ist ", "Paris", ". ", "Ende", "."]:
            out.extend(c.push(tok))
        tail = c.flush()
        if tail:
            out.append(tail)
        self.assertEqual(out, ["Die Hauptstadt ist Paris.", "Ende."])

    def test_decimal_not_split(self) -> None:
        c = SentenceChunker()
        self.assertEqual(_feed(c, "Pi ist etwa 3.14 wert."), ["Pi ist etwa 3.14 wert."])

    def test_abbreviation_not_split(self) -> None:
        c = SentenceChunker()
        self.assertEqual(
            _feed(c, "Das gilt z.B. für Hunde. Ende."),
            ["Das gilt z.B. für Hunde.", "Ende."],
        )

    def test_sentences_per_chunk_two(self) -> None:
        c = SentenceChunker(sentences_per_chunk=2)
        self.assertEqual(
            _feed(c, "Eins. Zwei. Drei. Vier."),
            ["Eins. Zwei.", "Drei. Vier."],
        )

    def test_flush_incomplete_tail(self) -> None:
        c = SentenceChunker()
        out = c.push("Ein vollstaendiger Satz. Ein unvollstaendiger")
        self.assertEqual(out, ["Ein vollstaendiger Satz."])
        self.assertEqual(c.flush(), "Ein unvollstaendiger")
        self.assertIsNone(c.flush())

    def test_no_premature_emit_on_trailing_terminator(self) -> None:
        # A terminator at the very end of the buffer must wait for the next char.
        c = SentenceChunker()
        self.assertEqual(c.push("Hallo."), [])  # could still be "Hallo.5"
        self.assertEqual(c.push(" Welt."), ["Hallo."])

    def test_empty(self) -> None:
        c = SentenceChunker()
        self.assertEqual(c.push(""), [])
        self.assertIsNone(c.flush())


if __name__ == "__main__":
    unittest.main()
