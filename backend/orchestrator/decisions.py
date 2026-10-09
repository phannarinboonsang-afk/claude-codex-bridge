import json
import re
from dataclasses import dataclass
from .types import DecisionError

_SECRET = re.compile(r'(?i)(?:\bBearer\s+\S+|\bsk-(?:ant-)?[A-Za-z0-9_-]{12,}|-----BEGIN .*PRIVATE KEY-----)')
_SECRET_NAME = re.compile(r'(?i)(?:api.?key|authorization|bearer|password|credential|secret|token)')
_ACTIONS = {'continue_agent','switch_agent','ask_user','wait_for_owner','done','blocked'}
_COMPLETION = {'goal_status','work_completed','validation_performed','remaining_limitations','final_summary'}


def reject_secrets(value):
    if isinstance(value, str) and _SECRET.search(value):
        raise DecisionError()
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_NAME.search(key):
                raise DecisionError()
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


@dataclass(frozen=True)
class Decision:
    action: str
    agent: str | None
    message_to_agent: str | None
    message_to_user: str | None
    reason: str
    checkpoint_update: dict
    protected_action_requested: dict | None
    completion: dict | None

    @classmethod
    def parse(cls, raw: str):
        try:
            if not isinstance(raw,str) or len(raw.encode()) > 32768:
                raise ValueError()
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value,dict) or set(value) != set(cls.__dataclass_fields__):
                raise ValueError()
            reject_secrets(value)
            if value['action'] not in _ACTIONS:
                raise ValueError()
            for name in ('message_to_agent','message_to_user'):
                text = value[name]
                if text is not None and (not isinstance(text,str) or not text.strip() or len(text)>18000):
                    raise ValueError()
            if not isinstance(value['reason'],str) or not value['reason'].strip() or len(value['reason'])>2000:
                raise ValueError()
            checkpoint=value['checkpoint_update']
            if not isinstance(checkpoint,dict) or len(json.dumps(checkpoint,allow_nan=False).encode())>16384:
                raise ValueError()
            executing=value['action'] in ('continue_agent','switch_agent')
            if executing:
                if value['agent'] not in ('codex','claude') or value['message_to_agent'] is None:
                    raise ValueError()
            elif value['agent'] is not None or value['message_to_agent'] is not None:
                raise ValueError()
            gate=value['protected_action_requested']
            if value['action']=='wait_for_owner':
                if not isinstance(gate,dict) or set(gate)!={'action','command','impact'} or any(not isinstance(t,str) or not t.strip() or len(t)>2000 for t in gate.values()):
                    raise ValueError()
            elif gate is not None:
                raise ValueError()
            completion=value['completion']
            if value['action']=='done':
                if not isinstance(completion,dict) or set(completion)!=_COMPLETION or any(not isinstance(t,str) or not t.strip() or len(t)>8000 for t in completion.values()):
                    raise ValueError()
            elif completion is not None:
                raise ValueError()
            if value['action'] in ('ask_user','blocked','wait_for_owner') and value['message_to_user'] is None:
                raise ValueError()
            return cls(**value)
        except (ValueError,TypeError,RecursionError):
            raise DecisionError() from None
