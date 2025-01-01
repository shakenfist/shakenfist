from enum import Enum


class PRIORITY(Enum):
    USER_WAITING = 10
    USER_FACING = 20
    BACKGROUND = 30
    BACKGROUND_HIGH_IO = 40
