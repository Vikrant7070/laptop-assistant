"""Nova - a friendly assistant that lives in your laptop, powered by a local Ollama model.

Run:  python -m nova.core.app   (opens http://127.0.0.1:5055; desktop.py starts the full desktop app)

Part B, Step E: this used to be one 4,600-line module; it is now a package, one module per job (see each
module's docstring). `app.<name>` still works for desktop.py and the tests: it is read from its home module on
every access. Patch or assign a name in its home module (`app.config.settings`), never on `app` itself -
tests/test_app_package.py checks that.
"""
# Import order: each module finds what it needs at import time already there.
from nova.core.app import eventbus, config, prompts, session, conversation, brains, skillrun, judging, body, api, voice_io, learning, host, integrations, background, resume, tasks_page, asking, managing, watching, scheduling  # noqa: F401

_MODULES = {m.__name__.rsplit(".", 1)[1]: m for m in (eventbus, config, prompts, session, conversation, brains, skillrun, judging, learning, api, host, voice_io, body, integrations, background, resume, tasks_page, asking, managing, watching, scheduling)}
_HOME = {name: _MODULES[mod] for mod, names in {
    'eventbus': ['Bus', 'BusyLock', 'HEARTBEAT', 'KEEP', 'MAX_QUEUE', 'bus', 'publish'],
    'config': ['ASSISTANT_NAME', 'CLOUD_HISTORY_MAX', 'DEFAULT_MODEL', 'MAX_EVENTS', 'MAX_HISTORY', 'MAX_STORED', 'PORT', 'PROMPT_TOKENS', 'ROOT', '_unspoken', 'brain', 'save_settings', 'settings'],
    'prompts': ['HEAR_MIXED', 'PERSONA', 'REASONING_CHOICES', 'TALK_ENGLISH', 'TALK_MIXED', 'active_tools', 'persona_text', 'reasoning_level', 'system_prompt', 'wake_phrase'],
    'session': ['ASK', 'FRIENDLY', 'MAX_THOUGHTS', 'ThoughtStream', '_cloud_q', '_cloud_wake', '_cloud_worker', '_labels', '_short_args', '_thought_lock', '_windows_display_name', 'ask_label', 'ask_text', 'coding_job_worker', 'is_risky', 'lock', 'skill_fix_note', 'skill_needs_ok', 'state', 'think', 'thoughts', 'thoughts_done'],
    'conversation': ['ACT_TOOLS', 'BODY_CLAIM_RE', 'CHECK_PROMPT', 'CODER_NOTE', 'CODER_QUESTION_NOTE', 'DEEP_TASK_TURNS', 'DONE_CLAIM_RE', 'FINAL_CHECK_WAIT', 'LOOK_FROM_STEP', 'MAX_TASK_TURNS', 'MAX_TURNS', 'PROMISE_RE', 'REPEATABLE', 'REPEAT_LIMIT', 'SLOW_TOOLS', 'STEP_CHECK_WAIT', 'STOPPED_REPLY', 'SpeechStream', 'Stopped', '_agent_loop', '_execute_one', '_is_real_user', 'agent_loop', 'check_stop', 'choose_local_route', 'history_limit', 'interruptible', 'local_route_for', 'more_tools', 'new_task', 'new_turn', 'no_self_greeting', 'not_open_note', 'route_task', 'stop_flag', 'stop_now', 'task_brain', 'task_log', 'trimmed_history', 'unless_stopped', 'wrong_app'],
    'brains': ['CANT', 'CLOUD_TAKES_OVER', 'MODES', 'after_step', 'at_answer', 'at_start', 'climb', 'cloud_helper', 'ready_info'],
    'skillrun': ['FAIL_RE', 'SKILL_BRAIN_TOOLS', 'SKILL_BRAIN_TURNS', '_failed', '_fill_args', '_finish_skill', '_pause_skill', '_sfill', '_short', '_skill_loop', '_skill_report', '_skill_say', '_trace', 'ask_skill_question', 'direct_call', 'plan_direct', 'resume_skill', 'run_skill', 'save_task_as_skill_now', 'skill_brain_step', 'skill_hints', 'skill_screen_ok'],
    'judging': ['_big_brain', 'ask_for_checks', 'check_step', 'execute', 'final_judge', 'look_check', 'propose_checks', 'settle'],
    'learning': ['FAILED_RE', 'LEARN_EVERY', 'LEARN_IDLE', 'QUESTION_SECS', 'REVIEW_PROMPT', 'SAVE_TOOL', '_asked_to_research', '_can_teach', '_covered_by_skill', '_say_final', '_takeover_pause', '_transcript', '_watch_answer', 'after_turn', 'finish_watching', 'knowledge_edit', 'knowledge_get', 'knowledge_page', 'learn_due', 'learn_now', 'learn_state', 'question_line', 'record_episode', 'review_turn', 'save_offered_skill', 'start_watching_now', 'take_question', 'teach_propose', 'watch_command', 'watch_note', 'why_review'],
    'api': ['_body', '_chosen_cloud', '_json_error', '_text', 'app', 'cloud_key', 'cloud_limits', 'cloud_models', 'cloud_usage', 'index', 'mic_level', 'only_this_laptop', 'setup', 'status'],
    'host': ['STARTUP_LNK', 'avatar', 'character_model', 'dev_character_ids', 'dev_joint_report', 'get_look', 'get_model', 'set_autostart', 'show_window', 'show_window_hook', 'voice_typing', 'wake_by_click'],
    'voice_io': ['ASKED_SECS', 'DEEP_SLEEP_AFTER', 'IDLE_NAP', 'JUST_STOP_RE', 'LISTEN_MAX', 'LISTEN_PAUSE', 'MIC_LEVEL', 'SLEEP_MAX_WORDS', '_start_conversation', '_still_listening', 'announce', 'apply_character', 'apply_listening', 'barge', 'barge_enabled', 'barge_flags', 'bubble', 'confirm_pending', 'convo', 'deep_sleep_due', 'ear_close', 'ear_cmds', 'ear_hooks', 'ear_listen', 'ear_listen_after_speech', 'ears', 'ears_changed', 'ears_on', 'events', 'expecting_reply', 'go_deep', 'go_to_sleep', 'heard', 'idle_watch', 'just_stop', 'listen_state', 'mouth', 'nap', 'nap_due', 'note_task', 'on_barge', 'on_request', 'on_voice', 'on_wake', 'push_event', 'refresh_barge', 'set_ears', 'silent_loop', 'silent_ok', 'sleep_body', 'speak', 'stopped_by_voice', 'transcriber', 'use_character_voice_and_name', 'wake_body', 'wants_sleep', 'warm_brain', 'whisper', 'windows_transcriber'],
    'body': ['BODY_WORDS', 'DANCE_NAMES', 'DANCE_WORDS', 'REACT_RULES', 'STYLE_WORDS', '_TIMES', '_cap_history', '_known_move', 'answer', 'body_command', 'look_first', 'pick_reaction', 'queue_new_move', 'queue_reaction', 'react_later'],
    'integrations': ['AWAY_IDLE', 'GREET_EVERY', 'HELP_EVERY', 'HELP_SECS', 'MOBILE_TAG', 'OFFER_SECS', 'PHONE_CALL_WAIT', 'PHONE_NOTE_MAX', 'PHONE_NOTE_SECS', 'PHONE_TAG', 'SECRETY', '_first_name', '_greeted', '_hide_codes', '_next_offer', '_phone_hint', '_speakable', 'email_allowed', 'forms_get', 'forms_set', 'gmail_get', 'gmail_set', 'help_answer', 'help_first', 'help_offer', 'help_queue', 'help_step', 'help_watch', 'job_done', 'mcp_get', 'mcp_set', 'mobile', 'mobile_confirm', 'mobile_get', 'mobile_info', 'mobile_message', 'mobile_set', 'notif_watcher', 'offer', 'offer_help', 'offers_waiting', 'on_notifications', 'on_presence', 'phone', 'phone_confirm', 'phone_get', 'phone_message', 'phone_note', 'phone_seen', 'phone_set', 'phone_status', 'presence_idle', 'presence_watch', 'ring_cue', 'say_only', 'screen_watch', 'sync_phone_banners', 'tailscale_url', 'take_help', 'take_offer', 'tell_away', 'user_away', 'workers_get', 'workers_set', 'write_text'],
    'background': ['NOVA_WINDOWS', 'SUGGEST_EVERY', 'SUGGEST_PROMPT', '_alerts', '_background', '_front_title', 'busy_now', 'chat', 'confirm', 'ear_idle', 'ear_page', 'ear_poll', 'ear_said', 'get_events', 'get_thoughts', 'offer_answer', 'PULSE_EVERY', '_sse', 'pulse', 'pulse_now', 'pulse_watch', 'stream', 'stream_events', 'voice_state', 'quit_api', 'quit_hook', 'quit_nova', 'reset', 'start_background', 'stop', 'suggest_api', 'suggest_now', 'suggest_state', 'suggest_watch', 'user_idle_secs', 'voice_stop', 'work_watch'],
}.items() for name in names}


def __getattr__(name):
    """app.<name> -> the current value in its home module (so a patch there is seen by every caller)."""
    mod = _HOME.get(name)
    if mod is None:
        for m in _MODULES.values():          # a module an app module imports (tools, llm, voice...)
            if name in vars(m):
                return vars(m)[name]
        raise AttributeError(f"module 'nova.core.app' has no attribute {name!r}")
    return getattr(mod, name)


def home_of(name):
    """The module that owns `name`."""
    return _HOME.get(name)
