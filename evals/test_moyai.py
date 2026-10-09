"""Run the saved Lens dataset against the explicitly configured deployment."""

import os

from lens import Lens

from evals.preflight import verify_ready


def test_moyai():
    verify_ready()
    lens = Lens(
        base_url=os.environ["LENS_BASE_URL"],
        api_key=os.environ["LENS_API_KEY"],
    )
    lens.evals.run(
        os.environ.get("MOYAI_EVAL_NAME", "moyai-coding-regressions")
    ).assert_passed()
