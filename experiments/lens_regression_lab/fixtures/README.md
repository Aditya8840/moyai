# Pinned original smoke checks

`coding_cases_ffcc83e.json` is the byte-for-byte `evals/coding_cases.json` from Moyai revision `ffcc83e054d45242a8a1a6465d1f775761ad6452`

SHA-256: `a5d1b87395b77552bbb0a3f67521dd95a8af46d36ec794a798a8e5e55f909a29`

The offline and real-agent comparison runners validate this hash before using it. Their legacy `current_passed` and `current_detected` fields refer to these original smoke checks. `stronger_passed` and `stronger_detected` use the actual checked-out `evals/coding_cases.json`, whose hash is recorded separately

This snapshot preserves the measured 8-of-12 mutation baseline after the production checks are improved. Do not update it when changing the production fixture. New experiments must retain their original fixture provenance so old results cannot silently change meaning
