"""Responses wire conversion using LiteLLM's maintained protocol transforms."""
import json


def to_completion(body):
    from litellm.responses.litellm_completion_transformation.transformation import LiteLLMCompletionResponsesConfig as Config
    if body.get('previous_response_id'):
        raise ValueError('Send full conversation input; server-side response IDs are not supported')
    payload = Config.transform_responses_api_request_to_chat_completion_request(
        model=body.get('model', ''), input=body.get('input', []),
        responses_api_request={k: v for k, v in body.items() if k not in {'model', 'input', 'stream'}}, stream=False)
    payload['stream'] = False
    return payload


def from_completion(value, request):
    from litellm.responses.litellm_completion_transformation.transformation import LiteLLMCompletionResponsesConfig as Config
    from litellm import ModelResponse
    result = Config.transform_chat_completion_response_to_responses_api_response(
        request_input=request.get('input', []), responses_api_request=request,
        chat_completion_response=ModelResponse(**value))
    return result.model_dump(exclude_none=True)


def message_events(response):
    sequence = 0
    def event(kind, **data):
        nonlocal sequence
        value = {'type': kind, 'sequence_number': sequence, **data}
        sequence += 1
        return ('event: ' + kind + '\ndata: ' + json.dumps(value) + '\n\n').encode()
    yield event('response.created', response={**response, 'status': 'in_progress', 'output': []})
    for index, item in enumerate(response.get('output', [])):
        yield event('response.output_item.added', output_index=index, item={**item, 'status': 'in_progress'})
        if item['type'] == 'message':
            for part_index, part in enumerate(item.get('content', [])):
                yield event('response.content_part.added', item_id=item['id'], output_index=index, content_index=part_index, part={**part, 'text': ''})
                yield event('response.output_text.delta', item_id=item['id'], output_index=index, content_index=part_index, delta=part.get('text', ''))
                yield event('response.output_text.done', item_id=item['id'], output_index=index, content_index=part_index, text=part.get('text', ''))
                yield event('response.content_part.done', item_id=item['id'], output_index=index, content_index=part_index, part=part)
        elif item['type'] == 'function_call':
            yield event('response.function_call_arguments.done', item_id=item['id'], output_index=index, arguments=item['arguments'])
        elif item['type'] == 'custom_tool_call':
            yield event('response.custom_tool_call_input.done', item_id=item['id'], output_index=index, input=item['input'])
        yield event('response.output_item.done', output_index=index, item=item)
    yield event('response.completed', response=response)
