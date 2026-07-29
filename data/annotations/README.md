# Human annotations

This directory contains local human-authored labels grouped by release.
Annotations are source data and are never overwritten by viewer-data builders.

Public viewer builds are read-only. When explicitly enabled for local
annotation, the viewer reads and writes `blog-v1/human-labels.json`; its API
validates every label against the matching released dataset and saves the
complete document atomically.
