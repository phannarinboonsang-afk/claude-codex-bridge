import json
import pytest
from backend.orchestrator.decisions import Decision, DecisionError
from backend.orchestrator.types import RunState, transition


def payload(action='continue_agent', **changes):
    value = dict(action=action, agent='codex', message_to_agent='Inspect synthetic work.',
                 message_to_user='Starting work.', reason='Owner goal', checkpoint_update={},
                 protected_action_requested=None, completion=None)
    if action not in ('continue_agent', 'switch_agent'):
        value.update(agent=None, message_to_agent=None)
    if action == 'done':
        value['completion'] = dict(goal_status='done', work_completed='Inspected',
            validation_performed='Synthetic test passed', remaining_limitations='None', final_summary='Done.')
    if action == 'wait_for_owner':
        value['protected_action_requested'] = dict(action='deployment', command='deploy candidate', impact='Production changes')
    value.update(changes)
    return json.dumps(value)


@pytest.mark.parametrize('action', ['continue_agent','switch_agent','ask_user','wait_for_owner','done','blocked'])
def test_decision_actions_validated(action):
    assert Decision.parse(payload(action)).action == action


def test_done_requires_completion():
    with pytest.raises(DecisionError):
        Decision.parse(payload('done', completion=None))


@pytest.mark.parametrize('raw', ['looks done', '{}', payload(extra=True), payload(agent='unknown'),
    payload(reason=''), payload(checkpoint_update={'bad':float('nan')}),
    payload(message_to_agent='x'*18001), payload(message_to_agent='Bearer synthetic-secret-123456')])
def test_invalid_output_no_fallback(raw):
    with pytest.raises(DecisionError) as error:
        Decision.parse(raw)
    assert 'synthetic-secret' not in str(error.value)


def test_explicit_state_transitions():
    assert transition(RunState.IDLE, 'start') == RunState.RUNNING
    assert transition(RunState.RUNNING, 'pause') == RunState.PAUSE_REQUESTED
    assert transition(RunState.PAUSE_REQUESTED, 'boundary') == RunState.PAUSED
    assert transition(RunState.RUNNING, 'ask_user') == RunState.PAUSED
    assert transition(RunState.PAUSED, 'continue') == RunState.RUNNING
    for state in (RunState.DONE, RunState.STOPPED):
        with pytest.raises(DecisionError):
            transition(state, 'continue')
