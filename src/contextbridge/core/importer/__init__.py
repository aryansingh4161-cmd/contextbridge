"""Import of untrusted context: packages, Markdown, pasted text, Claude Code transcripts, Claude
data exports (spec §9, M6). Everything is parsed → redacted → staged; nothing is written to project
memory until an explicit commit, and committed items carry provenance `imported`."""
