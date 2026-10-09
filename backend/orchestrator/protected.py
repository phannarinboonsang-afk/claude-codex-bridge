import re

_RULES=(
    ('destructive_reset',r'git\s+reset\b.*--hard'),('destructive_clean',r'git\s+clean\b'),
    ('force_push',r'git\s+push\b.*(?:--force|-f\b)'),('destructive_delete',r'\brm\s+.*(?:-r|--recursive)'),
    ('production_restart',r'\b(?:systemctl|service)\s+.*(?:restart|start|stop)|\brestart\s+(?:production|protected-service)'),
    ('production_deploy',r'\bdeploy\b|\bproduction\s+(?:write|migration)'),
    ('credential_change',r'\b(?:create|rotate|disclose|print)\s+(?:\w+\s+){0,2}(?:credentials?|api.?keys?|secrets?|bearer)'),
    ('permission_expansion',r'\b(?:sudo|chmod|chown|setcap)\b'),
)


def protected_request(instruction):
    for action,pattern in _RULES:
        if re.search(pattern,instruction,re.I): return action
    return None
