from __future__ import annotations

from quotegif.config import OllamaSettings
from quotegif.models import EpisodeRef
from quotegif.providers.prompts import build_identify_input
from quotegif.providers.response import parse_candidates, parse_json_response, system_prompt

_PROMPT_TEMPLATE = """\
{system}

{request}

Note: You do not have access to web search; base your answer on training knowledge only."""


class OllamaProvider:
    def __init__(self, settings: OllamaSettings, model_override: str | None = None) -> None:
        try:
            import ollama  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "ollama package not installed. Run: pip install 'quotegif[ollama]'"
            ) from e

        self._model = model_override or settings.model
        self._host = settings.host

    def identify(
        self,
        quote: str,
        *,
        show_hint: str | None = None,
        movie: bool = False,
        max_candidates: int = 5,
    ) -> list[EpisodeRef]:
        import ollama

        client = ollama.Client(host=self._host)
        request = build_identify_input(quote, show_hint=show_hint, movie=movie)
        prompt = _PROMPT_TEMPLATE.format(
            system=system_prompt(show_hint=show_hint),
            request=request,
        )
        response = client.generate(model=self._model, prompt=prompt)
        text = response.get("response", "").strip()

        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines
                if not line.startswith("```")
            ).strip()

        data = parse_json_response(text)
        return parse_candidates(data, quote)[:max_candidates]
