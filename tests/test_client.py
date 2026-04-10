"""
tests/test_client.py

Unit tests for src/agents/client.py.
Covers pure-logic helpers (JSON extraction, URL normalisation).
No real API calls are made in any test.
"""

import pytest


# ── _extract_json ──────────────────────────────────────────────────────────────

class TestExtractJson:
    def _call(self, text):
        from src.agents.client import _extract_json
        return _extract_json(text)

    def test_plain_json_object(self):
        result = self._call('{"key": "value"}')
        assert result == {"key": "value"}

    def test_plain_json_array(self):
        result = self._call('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_json_in_markdown_fence(self):
        text = '```json\n{"foo": "bar"}\n```'
        assert self._call(text) == {"foo": "bar"}

    def test_json_in_generic_fence(self):
        text = '```\n{"foo": "bar"}\n```'
        assert self._call(text) == {"foo": "bar"}

    def test_json_surrounded_by_prose(self):
        text = 'Here is the result: {"approved": true, "issues": []} — end'
        result = self._call(text)
        assert result["approved"] is True

    def test_nested_json_object(self):
        text = '{"children": [{"title": "A"}, {"title": "B"}]}'
        result = self._call(text)
        assert len(result["children"]) == 2

    def test_python_literal_true_false(self):
        # Models sometimes emit Python booleans
        text = "{'approved': True, 'issues': []}"
        result = self._call(text)
        assert result["approved"] is True

    def test_empty_string_raises(self):
        from src.agents.client import _extract_json
        with pytest.raises(ValueError, match="empty"):
            _extract_json("")

    def test_whitespace_only_raises(self):
        from src.agents.client import _extract_json
        with pytest.raises(ValueError):
            _extract_json("   ")

    def test_unparseable_raises_value_error(self):
        from src.agents.client import _extract_json
        with pytest.raises(ValueError, match="Could not extract"):
            _extract_json("this is not json at all !!!")

    def test_unicode_content_preserved(self):
        text = '{"title": "学会 Kubernetes"}'
        result = self._call(text)
        assert result["title"] == "学会 Kubernetes"

    def test_bom_stripped(self):
        text = '\ufeff{"key": 1}'
        result = self._call(text)
        assert result["key"] == 1


# ── _find_balanced_json_substring ─────────────────────────────────────────────

class TestFindBalancedJsonSubstring:
    def _call(self, text):
        from src.agents.client import _find_balanced_json_substring
        return _find_balanced_json_substring(text)

    def test_simple_object(self):
        assert self._call('prefix {"a":1} suffix') == '{"a":1}'

    def test_simple_array(self):
        assert self._call('pre [1,2,3] post') == '[1,2,3]'

    def test_nested_object(self):
        result = self._call('{"a": {"b": 2}}')
        assert result == '{"a": {"b": 2}}'

    def test_no_json_returns_none(self):
        assert self._call("no braces here") is None

    def test_string_with_escaped_braces(self):
        # Quoted braces should not confuse the parser
        result = self._call('{"key": "value with } brace"}')
        assert result is not None


# ── _parse_json_like ───────────────────────────────────────────────────────────

class TestParseJsonLike:
    def _call(self, text):
        from src.agents.client import _parse_json_like
        return _parse_json_like(text)

    def test_valid_json(self):
        assert self._call('{"x": 1}') == {"x": 1}

    def test_valid_list(self):
        assert self._call('[1, 2]') == [1, 2]

    def test_python_dict_literal(self):
        assert self._call("{'a': True}") == {"a": True}

    def test_invalid_returns_none(self):
        assert self._call("not json") is None

    def test_empty_returns_none(self):
        assert self._call("") is None

    def test_integer_returns_none(self):
        # Only dict/list accepted
        assert self._call("42") is None


# ── _normalise_openai_base_url ─────────────────────────────────────────────────

class TestNormaliseOpenAIBaseUrl:
    def _call(self, url):
        from src.agents.client import _normalise_openai_base_url
        return _normalise_openai_base_url(url)

    def test_empty_string_returns_default(self):
        assert self._call("") == "https://api.openai.com/v1"

    def test_bare_domain_gets_v1_appended(self):
        result = self._call("https://llm.example.com")
        assert result == "https://llm.example.com/v1"

    def test_url_with_existing_path_unchanged(self):
        result = self._call("https://llm.example.com/v1")
        assert result == "https://llm.example.com/v1"

    def test_url_with_trailing_slash_stripped(self):
        result = self._call("https://llm.example.com/v1/")
        assert result == "https://llm.example.com/v1"

    def test_root_path_gets_v1(self):
        result = self._call("https://llm.example.com/")
        assert result == "https://llm.example.com/v1"

    def test_custom_proxy_path_preserved(self):
        result = self._call("https://proxy.internal/openai/v1")
        assert result == "https://proxy.internal/openai/v1"


# ── _extract_openai_message_text ───────────────────────────────────────────────

class TestExtractOpenAIMessageText:
    def _call(self, message):
        from src.agents.client import _extract_openai_message_text
        return _extract_openai_message_text(message)

    def test_none_returns_empty(self):
        assert self._call(None) == ""

    def test_string_content(self):
        class FakeMsg:
            content = "hello"
        assert self._call(FakeMsg()) == "hello"

    def test_list_of_strings(self):
        class FakeMsg:
            content = ["hello", " world"]
        assert self._call(FakeMsg()) == "hello world"

    def test_list_of_dicts_with_text(self):
        class FakeMsg:
            content = [{"text": "part one"}, {"text": " part two"}]
        assert self._call(FakeMsg()) == "part one part two"

    def test_none_content_returns_empty(self):
        class FakeMsg:
            content = None
        assert self._call(FakeMsg()) == ""
