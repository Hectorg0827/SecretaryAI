/**
 * Display naming contract for the Lion BI application.
 * Lion BI is the product/platform. Lola is the AI assistant inside it.
 * Do not combine names in the primary platform wordmark or browser title.
 */
export const PLATFORM_NAME = 'Lion BI' as const;
export const PLATFORM_FULL_NAME = 'Lion Business Intelligence' as const;
export const ASSISTANT_NAME = 'Lola' as const;
export const ASSISTANT_DESCRIPTION = 'Your business intelligence assistant' as const;

export const BRAND_LABELS = {
  home: 'Home',
  conversations: 'Lola Conversations',
  assistant: ASSISTANT_NAME,
  voiceGreeting: 'Lola voice greeting',
  askAssistant: 'Ask Lola about your business…',
  appTitle: PLATFORM_NAME,
} as const;
