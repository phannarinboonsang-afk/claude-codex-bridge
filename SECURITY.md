# Security policy

Bridge is the policy enforcement point; prompts are not controls. Tasks are restricted to configured read roots and protected file classes. Codex v1 uses the Bridge read broker. Keep listener loopback and writer disabled by default.

Credentials, CLI login state, authorization headers, runtime databases, logs, and machine identity are local operator state. Do not commit or paste them into reports. AUTH_HEADER_FILE is a path to protected local file; scripts report presence only. Bridge does not pass an OpenAI API key to Codex.

Report suspected vulnerabilities privately with affected version, impact, and minimal reproduction. Never include live secrets or production logs.
