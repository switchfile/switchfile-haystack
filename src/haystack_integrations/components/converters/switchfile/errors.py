from typing import Any


class SwitchfileError(Exception):
    """
    An error returned by the switchfile API, or a conversion that did not finish.

    `code` is the stable error code from https://switchfile.io/docs/errors, for example `UNAUTHENTICATED`,
    `UNSUPPORTED_PAIR`, `PASSWORD_PROTECTED` or `TIMEOUT`. `status` is the HTTP status, if there was a response.
    """

    def __init__(self, code: str, message: str, status: int | None = None, details: Any = None) -> None:
        """
        Create the error.

        :param code: Error code of the API.
        :param message: Text for people. It can change, so match on `code` instead.
        :param status: HTTP status of the response, if any.
        :param details: The parsed error body, if any.
        """
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.details = details
