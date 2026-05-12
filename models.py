"""Public model registry for local benchmark verification."""

import os

from browser_use import ChatGoogle
from browser_use.llm import ChatAnthropic, ChatBrowserUse, ChatOpenAI


def _openai(model: str):
    return ChatOpenAI(model=model, api_key=os.getenv("OPENAI_API_KEY"))


def _anthropic(model: str):
    return ChatAnthropic(model=model, api_key=os.getenv("ANTHROPIC_API_KEY"))


def _google(model: str):
    return ChatGoogle(model=model, api_key=os.getenv("GOOGLE_API_KEY"))


def _openrouter(model: str):
    return ChatOpenAI(
        model=model,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )


MODELS = {
    "bu-1-0": lambda: ChatBrowserUse(model="bu-1-0"),
    "bu-2-0": lambda: ChatBrowserUse(model="bu-2-0"),
    "gpt-4.1": lambda: _openai("gpt-4.1"),
    "gpt-5": lambda: _openai("gpt-5"),
    "gpt-5-mini": lambda: _openai("gpt-5-mini"),
    "gpt-5.1-codex-mini": lambda: _openai("gpt-5.1-codex-mini"),
    "claude-3-5-haiku": lambda: _anthropic("claude-3-5-haiku"),
    "claude-haiku-4-5": lambda: _anthropic("claude-haiku-4-5"),
    "claude-sonnet-4-5": lambda: _anthropic("claude-sonnet-4-5"),
    "claude-sonnet-4-6": lambda: _anthropic("claude-sonnet-4-6"),
    "claude-opus-4-5": lambda: _anthropic("claude-opus-4-5"),
    "claude-opus-4-6": lambda: _anthropic("claude-opus-4-6"),
    "claude-opus-4-7": lambda: _anthropic("claude-opus-4-7"),
    "gemini-2.5-flash-lite": lambda: _google("gemini-2.5-flash-lite"),
    "gemini-2.5-flash": lambda: _google("gemini-2.5-flash"),
    "gemini-3-flash-preview": lambda: _google("gemini-3-flash-preview"),
    "gemini-3-pro-preview": lambda: _google("gemini-3-pro-preview"),
    "gemini-3.1-pro-preview": lambda: _google("gemini-3.1-pro-preview"),
    "gemini-3-1-pro-preview": lambda: _google("gemini-3.1-pro-preview"),
    "kimi-k2.5": lambda: _openrouter("moonshotai/kimi-k2.5"),
}
