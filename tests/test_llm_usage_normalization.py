from backend.chat.pipeline.llm_io import extract_usage_from_responses


def test_extract_usage_accepts_openai_chat_usage_keys():
    usage = extract_usage_from_responses({
        "usage": {
            "prompt_tokens": 123,
            "completion_tokens": 45,
            "total_tokens": 168,
        }
    })

    assert usage == {
        "input_tokens": 123,
        "cached_tokens": 0,
        "output_tokens": 45,
        "reasoning_tokens": 0,
        "completion_tokens": 45,
        "total_tokens": 168,
    }


def test_extract_usage_accepts_nested_token_details():
    usage = extract_usage_from_responses({
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens_details": {"reasoning_tokens": 7},
        }
    })

    assert usage["input_tokens"] == 100
    assert usage["cached_tokens"] == 30
    assert usage["output_tokens"] == 20
    assert usage["reasoning_tokens"] == 7
    assert usage["completion_tokens"] == 20
    assert usage["total_tokens"] == 120


def test_extract_usage_accepts_gemini_usage_keys():
    usage = extract_usage_from_responses({
        "usage": {
            "prompt_token_count": 11,
            "candidates_token_count": 22,
            "total_token_count": 33,
            "cached_content_token_count": 4,
        }
    })

    assert usage["input_tokens"] == 11
    assert usage["cached_tokens"] == 4
    assert usage["output_tokens"] == 22
    assert usage["completion_tokens"] == 22
    assert usage["total_tokens"] == 33
