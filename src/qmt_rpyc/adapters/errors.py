"""Internal failures at the provider boundary."""
class ProviderError(Exception):
    def __init__(self, category, operation, message, phase='pre_execution', outcome='not_executed'):
        super().__init__(message)
        self.category = category
        self.operation = operation
        self.phase = phase
        self.outcome = outcome


class ItemFailure(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
