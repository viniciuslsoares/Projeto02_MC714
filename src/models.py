# Mensagem trocada entre os nós.
from dataclasses import dataclass, field


@dataclass
class Message:
    type: str                                # tipo da mensagem (ex.: "ping")
    sender: int                              # id de quem enviou
    clock: int = 0                           # relógio de Lamport do emissor
    payload: dict = field(default_factory=dict)  # dados extras

    def to_dict(self):
        return {"type": self.type, "sender": self.sender,
                "clock": self.clock, "payload": self.payload}

    @staticmethod
    def from_dict(d):
        return Message(d["type"], d["sender"], d.get("clock", 0), d.get("payload", {}))
