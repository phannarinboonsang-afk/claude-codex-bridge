from enum import StrEnum


class DecisionError(ValueError):
    def __init__(self):
        super().__init__('Coordinator decision is invalid.')


class RunState(StrEnum):
    IDLE = 'IDLE'
    RUNNING = 'RUNNING'
    PAUSE_REQUESTED = 'PAUSE_REQUESTED'
    PAUSED = 'PAUSED'
    INTERRUPTING = 'INTERRUPTING'
    WAITING_FOR_OWNER = 'WAITING_FOR_OWNER'
    BLOCKED = 'BLOCKED'
    STOPPED = 'STOPPED'
    DONE = 'DONE'
    FAILED = 'FAILED'


TERMINAL = {RunState.DONE, RunState.STOPPED}
_TRANSITIONS = {
    (RunState.IDLE, 'start'): RunState.RUNNING,
    (RunState.RUNNING, 'pause'): RunState.PAUSE_REQUESTED,
    (RunState.PAUSE_REQUESTED, 'boundary'): RunState.PAUSED,
    (RunState.RUNNING, 'ask_user'): RunState.PAUSED,
    (RunState.RUNNING, 'interrupt'): RunState.INTERRUPTING,
    (RunState.PAUSE_REQUESTED, 'interrupt'): RunState.INTERRUPTING,
    (RunState.INTERRUPTING, 'boundary'): RunState.PAUSED,
    (RunState.PAUSED, 'continue'): RunState.RUNNING,
    (RunState.BLOCKED, 'recover'): RunState.RUNNING,
    (RunState.FAILED, 'recover'): RunState.RUNNING,
    (RunState.WAITING_FOR_OWNER, 'approve'): RunState.RUNNING,
    (RunState.WAITING_FOR_OWNER, 'input'): RunState.PAUSED,
    (RunState.RUNNING, 'done'): RunState.DONE,
}
for _state in RunState:
    if _state not in TERMINAL:
        _TRANSITIONS[_state, 'stop'] = RunState.STOPPED
        _TRANSITIONS[_state, 'blocked'] = RunState.BLOCKED
        _TRANSITIONS[_state, 'failed'] = RunState.FAILED
        _TRANSITIONS[_state, 'wait_for_owner'] = RunState.WAITING_FOR_OWNER


def transition(state: RunState, event: str) -> RunState:
    try:
        return _TRANSITIONS[RunState(state), event]
    except (KeyError, ValueError):
        raise DecisionError() from None
