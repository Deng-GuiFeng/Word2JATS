from types import SimpleNamespace

from word2jats.llm.usage import normalize_usage, summarize_usage, usage_dict
from word2jats.llm.client import _consume_stream


def test_provider_cache_partitions_have_identical_public_fields():
    common = dict(prompt_tokens=120, completion_tokens=7, total_tokens=127)
    qwen = normalize_usage({**common, "prompt_tokens_details": {"cached_tokens": 100}})
    deepseek = normalize_usage({**common, "prompt_cache_hit_tokens": 100,
                               "prompt_cache_miss_tokens": 20})
    assert qwen.pop("cache_miss_method") == "input_minus_cache_hit"
    assert deepseek.pop("cache_miss_method") == "response"
    assert qwen == deepseek


def test_no_usage_is_not_interpreted_as_measured_zero():
    totals = summarize_usage([{"usage": None}])
    assert not totals["complete"]
    assert totals["missing_fields"]["input_tokens"] == 1
    assert normalize_usage({})["cache_hit_tokens"] is None


def test_partition_inconsistencies_are_visible():
    assert normalize_usage({"prompt_tokens": 5, "prompt_cache_hit_tokens": 8})["errors"]
    assert normalize_usage({"prompt_tokens": 5, "prompt_cache_hit_tokens": 3,
                            "prompt_cache_miss_tokens": 4})["errors"]


def test_cost_and_provider_extensions_are_preserved():
    from openai.types import CompletionUsage
    raw = usage_dict(CompletionUsage(prompt_tokens=12, completion_tokens=2, total_tokens=14,
                                    prompt_cache_hit_tokens=10, prompt_cache_miss_tokens=2,
                                    cost=0.0001, currency="CNY"))
    summary = summarize_usage([{"usage": raw, "returned_model": "actual-model"}])
    assert summary["complete"]
    assert summary["costs_returned"][0]["fields"]["cost"] == 0.0001
    assert summary["returned_models"] == ["actual-model"]


def test_usage_received_before_truncation_is_not_lost():
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17)
    chunk = SimpleNamespace(usage=usage, id="id1", model="model1", choices=[
        SimpleNamespace(delta=SimpleNamespace(content="partial"), finish_reason="length")])
    try:
        _consume_stream([chunk])
    except RuntimeError as error:
        assert error._word2jats_usage is usage
        assert error._word2jats_response_meta["returned_model"] == "model1"
    else:
        raise AssertionError("truncated output must not be accepted")


def test_deepseek_schema_transport_uses_json_mode_and_keeps_constraints(monkeypatch, tmp_path):
    import sys
    from word2jats.llm.client import LLMClient
    observed = {}

    def create(**kwargs):
        observed.update(kwargs)
        return [SimpleNamespace(usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content='{"ok":true}'), finish_reason='stop')])]

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture')
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=Client))
    c = LLMClient('deepseek', cache_dir=str(tmp_path))
    fmt = {'type':'json_schema','json_schema':{'schema':{'type':'object','required':['ok']}}}
    try:
        answer, meta = c.request_json('JSON only', 'Return ok', response_format=fmt)
    finally:
        c.close()
    assert answer == {'ok': True}
    assert observed['response_format'] == {'type':'json_object'}
    assert '"required": ["ok"]' in observed['messages'][0]['content']
    assert meta['schema_transport'] == 'json-object-with-schema-v1'
