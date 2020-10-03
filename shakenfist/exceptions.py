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
class QueueTaskException(Exception):
    pass


class TaskUnknownTypeException(QueueTaskException):
    pass


class TaskImageFetchNoURLException(QueueTaskException):
    pass


class TaskNoInstanceException(QueueTaskException):
    pass
