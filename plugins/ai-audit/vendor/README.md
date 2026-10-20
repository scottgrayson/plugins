# Vendored tokenizer ranks

## `o200k_base.tiktoken`

Offline encoding ranks for OpenAI **o200k_base** (GPT-4o / o-series family).

| Field | Value |
|---|---|
| Source URL | https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken |
| SHA-256 | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |
| Lines | 199,998 mergeable ranks |
| Runtime | Loaded by `ai_audit.tokenize` via the `tiktoken` package (`Encoding` from vendored bytes; no network) |

Verify locally:

```bash
sha256sum vendor/o200k_base.tiktoken
# 446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d
```

## License notice (tiktoken / OpenAI encodings)

The `tiktoken` Python package is released under the **MIT License**
(https://github.com/openai/tiktoken). The public encoding rank files
hosted by OpenAI (including `o200k_base.tiktoken`) are distributed for
use with that tokenizer. This vendor directory redistributes only the
rank file for offline / air-gapped token counting; it does not modify
the ranks.

MIT License summary: permission is hereby granted, free of charge, to
any person obtaining a copy of this software and associated
documentation files, to deal in the software without restriction,
including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the software,
subject to including the copyright notice and this permission notice
in all copies or substantial portions.

Copyright (c) OpenAI.

Pinned companion package for CI / local install: `tiktoken==0.14.0`
(see `requirements.txt` and `.github/workflows/ci.yml`).
