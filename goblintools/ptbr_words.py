"""Embedded Brazilian-Portuguese wordlist for text-plausibility checks.

``data/palavras.txt.gz`` is derived from https://github.com/pythonprobr/palavras
(MPL-2.0, built from the LibreOffice pt_BR spell-check dictionary); see
``data/palavras.LICENSE``. Everything else in goblintools stays MIT.

Used only to corroborate the per-glyph substitution-cipher heuristic in
:mod:`goblintools.parser` — never as a hard gate.
"""
from __future__ import annotations

import gzip
import logging
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Iterable

from goblintools.log_policy import log_warning

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).with_name("data") / "palavras.txt.gz"

# Fallback vocabulary if the packaged wordlist is missing/unreadable.
_FALLBACK_EXTRA = {
    "edital", "licitacao", "pregao", "eletronico", "presencial", "contratacao",
    "proposta", "propostas", "empresa", "empresas", "objeto", "valor", "valores",
    "estimado", "estimada", "preco", "precos", "servico", "servicos", "material",
    "materiais", "aquisicao", "fornecimento", "prazo", "administracao", "municipio",
    "municipal", "prefeitura", "secretaria", "federal", "complementar", "artigo",
    "paragrafo", "inciso", "item", "itens", "anexo", "termo", "referencia",
    "habilitacao", "documento", "documentos", "proponente", "licitante", "licitantes",
    "comissao", "sessao", "publica", "publico", "contrato", "contratada", "contratante",
    "pagamento", "entrega", "quantidade", "unidade", "descricao", "especificacao",
    "total", "unitario", "global", "reais", "centavos", "mil", "milhao", "processo",
    "administrativo", "numero", "conforme", "previsto", "acordo", "forma", "devera",
    "deverao", "podera", "sera", "serao", "termos", "presente", "criterio",
    "julgamento", "menor", "maior", "garantia", "validade", "orcado", "orcamento",
}


def _fold(word: str) -> str:
    """Lowercase + strip diacritics so 'Ação' and 'acao' match the same entry."""
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _wordset() -> FrozenSet[str]:
    try:
        with gzip.open(_DATA_FILE, "rt", encoding="utf-8") as fh:
            words = {ln.strip() for ln in fh if len(ln.strip()) >= 2}
        if words:
            return frozenset(words)
        log_warning(logger, f"Embedded PT-BR wordlist {_DATA_FILE} is empty; using fallback.")
    except FileNotFoundError:
        log_warning(logger, f"Embedded PT-BR wordlist not found at {_DATA_FILE}; using fallback.")
    except OSError as e:
        log_warning(logger, f"Could not read embedded PT-BR wordlist {_DATA_FILE}: {e}; using fallback.")

    from goblintools.text_cleaner import DEFAULT_STOPWORDS

    return frozenset({_fold(w) for w in DEFAULT_STOPWORDS} | _FALLBACK_EXTRA)


def is_probably_portuguese(token: str) -> bool:
    """True if the ASCII-folded, lowercased *token* is in the embedded wordlist."""
    if not token:
        return False
    return _fold(token) in _wordset()


def dict_hit_rate(tokens: Iterable[str]) -> float:
    """Fraction of *tokens* that look like real Portuguese words (0.0 if empty)."""
    items = list(tokens)
    if not items:
        return 0.0
    ws = _wordset()
    return sum(1 for t in items if _fold(t) in ws) / len(items)
