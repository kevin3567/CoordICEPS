from agentdojo.strenum import StrEnum


class ModelsEnum(StrEnum):
    """Currently supported models."""

    GPT_4O_2024_05_13 = "gpt-4o-2024-05-13"
    """GPT-4o"""
    GPT_5_MINI = "gpt-5-mini"
    """GPT-5 Mini"""
    GPT_5_1 = "gpt-5.1"
    """GPT-5.1"""
    GPT_5_5 = "gpt-5.5"
    """GPT-5.5"""
    GPT_5_6_LUNA = "gpt-5.6-luna"
    """GPT-5.6 Luna"""
    GPT_5_6_TERRA = "gpt-5.6-terra"
    """GPT-5.6 Terra"""
    GPT_5_6_SOL = "gpt-5.6-sol"
    """GPT-5.6 Sol"""
    GPT_4O = "gpt-4o"
    """GPT-4o"""
    GPT_4O_MINI_2024_07_18 = "gpt-4o-mini-2024-07-18"
    """GPT-4o Mini"""
    GPT_4_0125_PREVIEW = "gpt-4-0125-preview"
    """GPT-4 Turbo Preview 0125"""
    GPT_3_5_TURBO_0125 = "gpt-3.5-turbo-0125"
    """GPT-3.5 Turbo 0125"""
    GPT_4_TURBO_2024_04_09 = "gpt-4-turbo-2024-04-09"
    """GPT-4 Turbo"""
    CLAUDE_3_OPUS_20240229 = "claude-3-opus-20240229"
    """Claude 3 Opus"""
    CLAUDE_3_SONNET_20240229 = "claude-3-sonnet-20240229"
    """Claude 3 Sonnet"""
    CLAUDE_3_5_SONNET_20240620 = "claude-3-5-sonnet-20240620"
    """Claude 3.5 Sonnet"""
    CLAUDE_3_5_SONNET_20241022 = "claude-3-5-sonnet-20241022"
    """Claude 3.5 Sonnet October"""
    CLAUDE_3_7_SONNET_20250219 = "claude-3-7-sonnet-20250219"
    """Claude 3.7 Sonnet"""
    CLAUDE_3_7_SONNET_20250219_THINKING_16000 = "claude-3-7-sonnet-20250219-thinking-16000"
    """Claude 3.7 Sonnet"""
    CLAUDE_3_HAIKU_20240307 = "claude-3-haiku-20240307"
    """Claude 3 Haiku"""
    CLAUDE_4_SONNET = "claude-sonnet-4-20250514"
    """Claude 4 Sonnet"""
    CLAUDE_4_5_SONNET_20250929 = "claude-sonnet-4-5-20250929"
    """Claude 4.5 Sonnet"""
    COMMAND_R_PLUS = "command-r-plus"
    """Command R Plus"""
    COMMAND_R = "command-r"
    """Command R"""
    MISTRALAI_MIXTRAL_8X7B_INSTRUCT_V0_1 = "mistralai/Mixtral-8x7B-Instruct-v0.1"
    """MistralAI Mixtral 8x7B Instruct v0.1"""
    LLAMA_3 = "meta-llama/Llama-3-70b-chat-hf"
    """Llama 3"""
    GEMINI_1_5_PRO_002 = "gemini-1.5-pro-002"
    """Gemini 1.5 Pro 002"""
    GEMINI_1_5_PRO_001 = "gemini-1.5-pro-001"
    """Gemini 1.5 Pro 001"""
    GEMINI_1_5_FLASH_002 = "gemini-1.5-flash-002"
    """Gemini 1.5 Flash 002"""
    GEMINI_1_5_FLASH_001 = "gemini-1.5-flash-001"
    """Gemini 1.5 Flash 001"""
    GEMINI_2_0_FLASH_EXP = "gemini-2.0-flash-exp"
    """Gemini 2.0 Flash Exp"""
    GEMINI_2_0_FLASH_001 = "gemini-2.0-flash-001"
    """Gemini 2.0 Flash 001"""
    GEMINI_2_5_FLASH_PREVIEW_04_17 = "gemini-2.5-flash-preview-04-17"
    """Gemini 2.5 Flash Preview 04/17"""
    GEMINI_2_5_PRO_PREVIEW_05_06 = "gemini-2.5-pro-preview-05-06"
    """Gemini 2.5 Pro Preview 05/06"""
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    """Gemini 2.5 Flash"""
    LOCAL = "local"
    """Local model"""
    VLLM_PARSED = "vllm_parsed"
    """VLLM parsed model"""
    DEEPSEEK_V4_PRO = "deepseek/deepseek-v4-pro"
    """Deepseek V4 Pro"""
    DEEPSEEK_V4_FLASH = "deepseek/deepseek-v4-flash"
    """Deepseek V4 Flash"""
    GEMMA_4_31B_IT = "google/gemma-4-31b-it"
    """Gemma 4 31B IT"""
    GEMMA_4_26B_A4B_IT = "google/gemma-4-26b-a4b-it"
    """Gemma 4 26B A4B IT"""
    MINIMAX_M2_7 = "minimax/minimax-m2.7"
    """Minimax M2.7"""
    MINIMAX_M3 = "minimax/minimax-m3"
    """Minimax M3"""
    QWEN_3_6_27B = "qwen/qwen3.6-27b"
    """Qwen 3.6 27B"""
    QWEN_3_6_35B_A3B = "qwen/qwen3.6-35b-a3b"
    """Qwen 3.6 35B A3B"""
    GLM_5_1 = "z-ai/glm-5.1"
    """GLM 5.1"""
    GLM_5_2 = "z-ai/glm-5.2"
    """GLM 5.2"""
    KIMI_K2_5 = "moonshotai/kimi-k2.5"
    "Kimi K2.5"
    KIMI_K2_6 = "moonshotai/kimi-k2.6"
    "Kimi K2.6"


MODEL_PROVIDERS = {
    ModelsEnum.GPT_4O: "openai",
    ModelsEnum.GPT_4O_2024_05_13: "openai",
    ModelsEnum.GPT_5_MINI: "openai",
    ModelsEnum.GPT_4O_MINI_2024_07_18: "openai",
    ModelsEnum.GPT_3_5_TURBO_0125: "openai",
    ModelsEnum.GPT_4_0125_PREVIEW: "openai",
    ModelsEnum.GPT_4_TURBO_2024_04_09: "openai",
    ModelsEnum.GPT_5_1: "openai",
    ModelsEnum.GPT_5_5: "cli_proxy",
    ModelsEnum.GPT_5_6_LUNA: "cli_proxy",
    ModelsEnum.GPT_5_6_TERRA: "cli_proxy",
    ModelsEnum.GPT_5_6_SOL: "cli_proxy",
    ModelsEnum.CLAUDE_3_OPUS_20240229: "anthropic",
    ModelsEnum.CLAUDE_3_SONNET_20240229: "anthropic",
    ModelsEnum.CLAUDE_3_5_SONNET_20240620: "anthropic",
    ModelsEnum.CLAUDE_3_5_SONNET_20241022: "anthropic",
    ModelsEnum.CLAUDE_3_7_SONNET_20250219: "anthropic",
    ModelsEnum.CLAUDE_3_7_SONNET_20250219_THINKING_16000: "anthropic",
    ModelsEnum.CLAUDE_3_HAIKU_20240307: "anthropic",
    ModelsEnum.CLAUDE_4_5_SONNET_20250929: "anthropic",
    ModelsEnum.COMMAND_R_PLUS: "cohere",
    ModelsEnum.COMMAND_R: "cohere",
    ModelsEnum.MISTRALAI_MIXTRAL_8X7B_INSTRUCT_V0_1: "together",
    ModelsEnum.LLAMA_3: "together-prompting",
    ModelsEnum.GEMINI_1_5_PRO_001: "google",
    ModelsEnum.GEMINI_1_5_PRO_002: "google",
    ModelsEnum.GEMINI_1_5_FLASH_001: "google",
    ModelsEnum.GEMINI_1_5_FLASH_002: "google",
    ModelsEnum.GEMINI_2_0_FLASH_EXP: "google",
    ModelsEnum.GEMINI_2_0_FLASH_001: "google",
    ModelsEnum.GEMINI_2_5_FLASH_PREVIEW_04_17: "google",
    ModelsEnum.GEMINI_2_5_PRO_PREVIEW_05_06: "google",
    ModelsEnum.GEMINI_2_5_FLASH: "google",
    ModelsEnum.LOCAL: "local",
    ModelsEnum.VLLM_PARSED: "vllm_parsed",
    ModelsEnum.DEEPSEEK_V4_PRO: "openrouter",
    ModelsEnum.DEEPSEEK_V4_FLASH: "openrouter",
    ModelsEnum.GEMMA_4_31B_IT: "openrouter",
    ModelsEnum.GEMMA_4_26B_A4B_IT: "openrouter",
    ModelsEnum.MINIMAX_M2_7: "openrouter",
    ModelsEnum.MINIMAX_M3: "openrouter",
    ModelsEnum.QWEN_3_6_27B: "openrouter",
    ModelsEnum.QWEN_3_6_35B_A3B: "openrouter",
    ModelsEnum.GLM_5_1: "openrouter",
    ModelsEnum.GLM_5_2: "openrouter",
    ModelsEnum.KIMI_K2_5: "openrouter",
    ModelsEnum.KIMI_K2_6: "openrouter",
}


MODEL_NAMES = {
    "gpt-4o": "GPT-4",
    "gpt-4o-2024-05-13": "GPT-4",
    "gpt-5-mini": "GPT-5-Mini",
    "gpt-5.1": "GPT-5.1",
    "gpt-5.5": "GPT-5.5",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-4o-mini-2024-07-18": "GPT-4",
    "gpt-3.5-turbo-0125": "GPT-3.5",
    "gpt-4-turbo-2024-04-09": "GPT-4",
    "gpt-4-0125-preview": "GPT-4",
    "claude-3-opus-20240229": "Claude",
    "claude-3-sonnet-20240229": "Claude",
    "claude-3-5-sonnet-20240620": "Claude",
    "claude-3-5-sonnet-20241022": "Claude",
    "claude-3-7-sonnet-20250219": "Claude",
    "claude-3-7-sonnet-20250219-thinking-16000": "Claude",
    "claude-3-haiku-20240307": "Claude",
    "claude-sonnet-4-20250514": "Claude",
    "claude-sonnet-4-5-20250929": "Claude",
    "command-r-plus": "Command R+",
    "command-r": "Command R",
    "mistralai/Mixtral-8x7B-Instruct-v0.1": "Mixtral",
    "meta-llama/Llama-3-70b-chat-hf": "AI assistant",
    "gemini-1.5-pro-002": "AI model developed by Google",
    "gemini-1.5-pro-001": "AI model developed by Google",
    "gemini-1.5-flash-002": "AI model developed by Google",
    "gemini-1.5-flash-001": "AI model developed by Google",
    "gemini-2.0-flash-exp": "AI model developed by Google",
    "gemini-2.0-flash-001": "AI model developed by Google",
    "gemini-2.5-flash-preview-04-17": "AI model developed by Google",
    "gemini-2.5-pro-preview-05-06": "AI model developed by Google",
    "gemini-2.5-flash": "AI model developed by Google",
    "local": "Local model",
    "vllm_parsed": "Local model",
    "deepseek/deepseek-v4-pro": "Deepseek",
    "deepseek/deepseek-v4-flash": "Deepseek",
    "google/gemma-4-31b-it": "Gemma",
    "google/gemma-4-26b-a4b-it": "Gemma",
    "minimax/minimax-m2.7": "MiniMax",
    "minimax/minimax-m3": "MiniMax",
    "qwen/qwen3.6-27b": "Qwen",
    "qwen/qwen3.6-35b-a3b": "Qwen",
    "z-ai/glm-5.1": "GLM",
    "z-ai/glm-5.2": "GLM",
    "moonshotai/kimi-k2.5": "Kimi",
    "moonshotai/kimi-k2.6": "Kimi",
}
