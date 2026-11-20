class AgentError(Exception):
    """An expected error with a user-facing explanation."""


class BusyError(AgentError):
    pass


class RetryableError(AgentError):
    pass


class ValidationError(RetryableError):
    pass


class NeedsInput(AgentError):
    pass


class Cancelled(AgentError):
    pass


class OutputTruncated(NeedsInput, RetryableError):
    pass


class AwaitApproval(AgentError):
    def __init__(self, approval):
        self.approval = approval
        super().__init__('本章已生成，请阅读并确认后再继续。')
