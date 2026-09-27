"""aia-pdf-split: POC to evaluate Google Document AI Custom Splitter as a document
intelligence layer for physical PDF splitting.

Package layout:
- config: environment / settings
- providers: the provider abstraction (google adapter + mock)
- pdf: physical PDF splitting + verification
- corpus: ground-truth corpus building + probing
- benchmark: provider-agnostic harness + scoring
"""

__version__ = "0.1.0"
