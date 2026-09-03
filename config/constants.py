from enum import Enum

class StateEnum(Enum):
    CLASSIFY = "classify"
    APPOINTMENT = "appointment"
    CONSULT = "consult"
    OTHER = "other"
    
class SharedState:
    def __init__(self):
        self.value = StateEnum.CLASSIFY
