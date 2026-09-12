"""
Single source of truth for which Gemini model this repo calls.

Why this file exists (2026-09-12): every call site used to hardcode its own
copy of the model string ("gemini-1.5-flash:generateContent"). Google fully
shut down all Gemini 1.0/1.5 models, and because the string was duplicated
across four files (qc_vision_provider.py, rpas_validator.py,
compliance_validator.py, bricks_ai_dispatch.py) nobody caught that all four
had been 404ing in production — silently, since every one of those call
sites is designed to fail open (catch the error, fall back to a
non-LLM/no-op path) rather than block the pipeline. See CLAUDE.md's
Post-Production Pipeline / QC / RPAS sections for the incident writeup.

Fix: one env-overridable constant, imported everywhere a Gemini model name
is needed. The next time Google retires a model, this is a one-line (or one
env var) change instead of a four-file grep-and-replace.
"""
import os

# Default kept current as of 2026-09-12 — verified via live web research at
# that time: gemini-3.1-flash-lite (suggested as of this file's original
# authoring) turned out to already be deprecated (announced retirement,
# shutdown scheduled 2027-05-07) with gemini-3.5-flash-lite as Google's own
# recommended replacement — a GA, stable, no-shutdown-announced flash-lite
# model released 2026-07-21, priced for exactly this repo's use case
# (classification / vision scoring, not conversational). Re-verify against
# https://ai.google.dev/gemini-api/docs/deprecations before assuming this
# default is still current.
_DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def get_gemini_model() -> str:
    """Read the current model name (env override wins), live on every call —
    never cached at import time, so GEMINI_MODEL_NAME can be changed (e.g. by
    a test, or an ops emergency override) without an app restart."""
    return os.environ.get("GEMINI_MODEL_NAME", _DEFAULT_GEMINI_MODEL)


# Convenience constant for call sites that only need the default at import
# time (e.g. docstrings/logging) — prefer get_gemini_model() /
# gemini_generate_content_url() for anything that actually calls the API.
GEMINI_MODEL = _DEFAULT_GEMINI_MODEL


def gemini_generate_content_url(model: str | None = None) -> str:
    """Build the generateContent endpoint URL for the given model, or the
    live env-resolved default when none is passed."""
    return f"{GEMINI_API_BASE}/{model or get_gemini_model()}:generateContent"


# ---------------------------------------------------------------------------
# Image-generation/editing model -- a SEPARATE default from get_gemini_model()
# above. The flash-lite text/vision-scoring model used for QC/RPAS/compliance/
# dispatch-explainability cannot return an edited image; image output needs a
# distinct Gemini image model family.
#
# Added 2026-09-12 for object_removal_service.py's Gemini fallback engine.
# Re-verified same day: gemini-2.5-flash-image's shutdown is confirmed for
# 2026-10-02 (~3 weeks out), and Google's own migration guidance names
# gemini-3.1-flash-image-preview as the official replacement -- not a
# community guess, this is the documented "go-to image generation model"
# in Google's current image-generation guide. Switched the default to it
# despite the "-preview" suffix (which normally argues against defaulting
# to a preview model, per get_gemini_model()'s reasoning above) because the
# alternative is defaulting to a model that will be dead in three weeks --
# the greater risk here is the confirmed shutdown, not the preview label.
# Still env-overridable via GEMINI_IMAGE_MODEL_NAME for whenever this
# preview graduates to GA (or gets replaced again). Re-verify against
# https://ai.google.dev/gemini-api/docs/deprecations before assuming this
# default is still current -- this space moved three times in six weeks
# as of this date.
_DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image-preview"


def get_gemini_image_model() -> str:
    """Read the current image-generation/editing model name (env override
    wins), live on every call -- same never-cached-at-import convention as
    get_gemini_model()."""
    return os.environ.get("GEMINI_IMAGE_MODEL_NAME", _DEFAULT_GEMINI_IMAGE_MODEL)
