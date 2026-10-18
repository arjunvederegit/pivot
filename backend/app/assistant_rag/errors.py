"""Ask Pivot's failure vocabulary, on Pivot's existing error envelope.

Every class is a `DomainError`, so the handler already registered in `app.main` renders it
as `{"error": {"code", "message", "request_id"}}` with the right status. The assistant never
lets a provider exception escape as a bare 500: an unreachable Ollama, a model that was never
pulled, a timeout and a full in-flight slot are each a distinct, actionable code.
"""

from __future__ import annotations

from app.core.errors import DomainError


class AssistantError(DomainError):
    status_code = 500
    code = "assistant_error"


class AssistantDisabledError(AssistantError):
    """The feature flag is off. A 404, so a disabled deployment does not advertise the route."""

    status_code = 404
    code = "assistant_disabled"


class AssistantLocalOnlyError(AssistantError):
    status_code = 403
    code = "assistant_local_only"


class AssistantValidationError(AssistantError):
    """A request that passed schema validation but names something that does not exist
    (an unknown fictional team) or uses dev-only options with them disabled."""

    status_code = 422
    code = "validation_error"


class AssistantDependencyError(AssistantError):
    status_code = 503
    code = "assistant_dependencies_missing"


class CorpusUnavailableError(AssistantError):
    status_code = 503
    code = "corpus_unavailable"


class ProviderUnavailableError(AssistantError):
    status_code = 503
    code = "provider_unavailable"


class ModelUnavailableError(AssistantError):
    status_code = 503
    code = "model_unavailable"


class AssistantBusyError(AssistantError):
    status_code = 503
    code = "assistant_busy"


class ProviderTimeoutError(AssistantError):
    status_code = 504
    code = "provider_timeout"


class RequestCancelledError(AssistantError):
    """The client went away mid-answer and the work was cancelled. 499 is nginx's
    "client closed request": nobody reads this response, but the log line does."""

    status_code = 499
    code = "request_cancelled"


class AssistantInternalError(AssistantError):
    """Anything unexpected inside the pipeline — still enveloped, still with a request id."""

    status_code = 500
    code = "assistant_internal_error"
