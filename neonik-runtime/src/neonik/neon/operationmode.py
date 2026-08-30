from enum import Enum

from .helper import NeonException


class OperationMode(Enum):
    LOCAL = 1
    LOCAL_VIRTUAL = 2
    DISTRIBUTED = 3

    def is_local(self) -> bool:
        return self == OperationMode.LOCAL or self == OperationMode.LOCAL_VIRTUAL

    @staticmethod
    def from_name(name: str) -> 'OperationMode':
        for mode in OperationMode:
            if mode.name == name:
                return mode
        raise NeonException(f"Unkown mode: {name}")

    @staticmethod
    def from_local_virtual(local: bool, virtual: bool) -> 'OperationMode':
        if local:
            if virtual:
                return OperationMode.LOCAL_VIRTUAL
            else:
                return OperationMode.LOCAL
        else:
            if not virtual:
                return OperationMode.DISTRIBUTED
            else:
                raise Exception("Illegal combination: local=False, virtual=True")