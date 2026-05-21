// TUI i18n type definitions
export type Locale = 'en' | 'zh' | 'zh-hant' | 'ja' | 'de' | 'es' | 'fr' | 'ko';

export interface TUITranslations {
  session: {
    available_tools: string;
    available_skills: string;
    system_prompt: string;
    mcp_servers: string;
    scanning_skills: string;
    no_system_prompt: string;
    more_categories: string;
    more_toolsets: string;
    tools_count: string;
    skills_count: string;
    help_commands: string;
    behind: string;
    to_update: string;
  };

  thinking: {
    thinking: string;
    tool_calls: string;
    progress: string;
    spawned: string;
    delegate_task: string;
    spawn_tree: string;
    activity: string;
  };

  prompts: {
    always_allow: string;
    allow_once: string;
    allow_session: string;
  };

  agents: {
    budget: string;
    tool_calls: string;
    output: string;
    progress: string;
    summary: string;
    last_turn: string;
    files: string;
  };

  branding: {
    nous_research: string;
    messenger: string;
  };

  status: {
    running: string;
    ready: string;
  };

  statusBar: {
    voiceOn: string;
    voiceOff: string;
    voiceRec: string;
    voiceStt: string;
  };

  sessionCmd: {
    newStarted: string;
    titleSet: string;
    titleSetQueued: string;
    titleSetFailed: string;
  };

  confirm: {
    newSessionTitle: string;
    clearSessionTitle: string;
    newSessionConfirm: string;
    clearSessionConfirm: string;
    cancelLabel: string;
    sessionDetail: string;
  };

  voice: {
    modeOff: string;
    helpTts: string;
    helpOff: string;
  };

  subagent: {
    queued: string;
    running: string;
    done: string;
    error: string;
    cancelled: string;
    interrupted: string;
  };
}
