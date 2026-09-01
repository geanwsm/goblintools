"""Tests for the embedded PT-BR wordlist helpers."""
from pathlib import Path

import goblintools.ptbr_words as ptw
from goblintools.ptbr_words import dict_hit_rate, is_probably_portuguese


def test_is_probably_portuguese_accepts_real_words():
    assert is_probably_portuguese("licitacao")
    assert is_probably_portuguese("Contratação")  # folded + lowercased internally
    assert is_probably_portuguese("edital")


def test_is_probably_portuguese_rejects_cipher_tokens():
    assert not is_probably_portuguese("EÍesentadas")
    assert not is_probably_portuguese("couvocnronto")
    assert not is_probably_portuguese("")


def test_dict_hit_rate_clean_vs_cipher():
    clean = "a prefeitura municipal torna publico o edital de licitacao para contratacao".split()
    cipher = "EÍesentadas couvocnronto soQdaJHP Edilal Convocalorio Esrado Comparalivo".split()
    assert dict_hit_rate(clean) >= 0.7
    assert dict_hit_rate(cipher) <= 0.2
    assert dict_hit_rate([]) == 0.0


def test_wordset_falls_back_when_data_file_missing(monkeypatch, tmp_path):
    """A missing/unreadable packaged wordlist degrades to stopwords + domain terms
    instead of raising."""
    monkeypatch.setattr(ptw, "_DATA_FILE", Path(tmp_path) / "nope.txt.gz")
    ptw._wordset.cache_clear()
    try:
        ws = ptw._wordset()
        assert "edital" in ws and "licitacao" in ws  # domain fallback
        assert ptw._fold("de") in ws                 # stopword fallback
        assert "soqdajhp" not in ws
    finally:
        ptw._wordset.cache_clear()  # restore the real wordlist for other tests
