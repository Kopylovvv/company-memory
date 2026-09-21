"""Domain errors for the cases module, mapped to HTTP responses in app.api.error_handlers."""


class CaseError(Exception):
    code = "case_error"


class CaseNotFoundError(CaseError):
    code = "case_not_found"

    def __init__(self, case_id: str) -> None:
        super().__init__(f"Case '{case_id}' was not found.")


class CaseAlreadyConfirmedError(CaseError):
    code = "case_already_confirmed"

    def __init__(self, case_id: str) -> None:
        super().__init__(f"Case '{case_id}' is already confirmed and cannot be changed this way.")


class InsufficientDataError(CaseError):
    code = "insufficient_data"

    def __init__(self, case_id: str) -> None:
        super().__init__(
            f"Case '{case_id}' is missing equipment or all of symptom/action/result, "
            "so it cannot be confirmed yet."
        )
