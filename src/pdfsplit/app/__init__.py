"""FastAPI web app for the PDF splitter POC.

Serves the upload -> split -> correct -> save journey. Reuses the existing
SplitterProvider / SplitResult contract and the physical split + verification
in pdfsplit.pdf. No parallel document model.
"""
