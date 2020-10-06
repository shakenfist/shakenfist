class HTTPError(Exception):
    pass


class VersionSpecificationError(Exception):
    pass


class SchedulerException(Exception):
    pass


class CandidateNodeNotFoundException(SchedulerException):
    pass


class LowResourceException(SchedulerException):
    pass


class AbortInstanceStartException(SchedulerException):
    pass


class DatabaseException(Exception):
    pass


class LockException(DatabaseException):
    pass


class WriteException(DatabaseException):
    pass


class ReadException(DatabaseException):
    pass


class VirtException(Exception):
    pass


class NoDomainException(VirtException):
    pass


class FlagException(Exception):
    pass


# Tasks
class TaskException(Exception):
    pass


class UnknownTaskException(TaskException):
    pass


class NoURLImageFetchTaskException(TaskException):
    pass


class NoInstanceTaskException(TaskException):
    pass


class NetworkNotListTaskException(TaskException):
    pass


class NoNextStateTaskException(TaskException):
    pass
