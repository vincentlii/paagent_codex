"""Offline, auditable research protocols for PA Agent."""

from pa_agent.experiments.pa_vpa_aabb import (
    AABB_PROTOCOL_VERSION,
    PaVpaAabbRunner,
    freeze_experiment_input,
)
from pa_agent.experiments.backend_aabb import (
    BACKEND_AABB_PROTOCOL_VERSION,
    BackendAabbRunner,
    freeze_backend_experiment_input,
    summarize_backend_experiments,
)
from pa_agent.experiments.backend_service import BackendAabbExperimentService
from pa_agent.experiments.backend_review import (
    BlindedBackendReview,
    build_blinded_backend_review,
    score_blinded_backend_review,
    unblind_backend_scores,
)

__all__ = [
    "AABB_PROTOCOL_VERSION",
    "PaVpaAabbRunner",
    "freeze_experiment_input",
    "BACKEND_AABB_PROTOCOL_VERSION",
    "BackendAabbRunner",
    "freeze_backend_experiment_input",
    "BackendAabbExperimentService",
    "summarize_backend_experiments",
    "BlindedBackendReview",
    "build_blinded_backend_review",
    "score_blinded_backend_review",
    "unblind_backend_scores",
]
