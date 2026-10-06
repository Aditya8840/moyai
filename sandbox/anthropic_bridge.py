"""Claude SDK wire adapter to the existing authenticated, accounted model broker.

Only the local wire protocol changes. Model choice, skills, memory, credentials,
request limits and billing remain enforced by the existing server endpoint.
"""
import json


def to_completion(body):
    messages = []
    system = body.get('system')
    if system:
        messages.append({'role': 'system', 'content': system if isinstance(system, str) else
                         '\n'.join(b['text'] for b in system if b.get('type') == 'text')})
    for message in body['messages']:
        content = message['content']
        if isinstance(content, str):
            messages.append({'role': message['role'], 'content': content})
            continue
        parts, calls, results = [], [], []
        for block in content:
            kind = block['type']
            if kind == 'text':
                parts.append({'type': 'text', 'text': block['text']})
            elif kind == 'image':
                source = block['source']
                url = source['url'] if source['type'] == 'url' else f"data:{source['media_type']};base64,{source['data']}"
                parts.append({'type': 'image_url', 'image_url': {'url': url}})
            elif kind == 'tool_use':
                calls.append({'id': block['id'], 'type': 'function', 'function': {
                    'name': block['name'], 'arguments': json.dumps(block['input'])}})
            elif kind == 'tool_result':
                result = block.get('content', '')
                # Preserve structured tool results, including error markers.
                results.append({'role': 'tool', 'tool_call_id': block['tool_use_id'],
                                'content': result if isinstance(result, str) else json.dumps(result)})
            elif kind not in {'thinking', 'redacted_thinking'}:
                raise ValueError('Unsupported Claude content block: ' + kind)
        messages.extend(results)
        if parts or calls:
            item = {'role': message['role'], 'content': parts or None}
            if calls:
                item['tool_calls'] = calls
            messages.append(item)
    payload = {'messages': messages, 'stream': False, 'max_tokens': body.get('max_tokens', 8192)}
    for key in ('temperature', 'top_p'):
        if key in body:
            payload[key] = body[key]
    if body.get('tools'):
        payload['tools'] = [{'type': 'function', 'function': {
            'name': t['name'], 'description': t.get('description', ''), 'parameters': t['input_schema']}}
            for t in body['tools']]
    choice = body.get('tool_choice', {})
    if choice.get('type') in {'auto', 'none', 'any'}:
        payload['tool_choice'] = {'any': 'required'}.get(choice['type'], choice['type'])
    elif choice.get('type') == 'tool':
        payload['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
    return payload


def from_completion(value):
    choice = value['choices'][0]
    message = choice['message']
    blocks = []
    if message.get('content'):
        blocks.append({'type': 'text', 'text': message['content']})
    for call in message.get('tool_calls') or []:
        blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'],
                       'input': json.loads(call['function']['arguments'])})
    usage = value.get('usage') or {}
    return {'id': value.get('id', 'msg_moyai'), 'type': 'message', 'role': 'assistant',
            'model': value.get('model', ''), 'content': blocks,
            'stop_reason': {'tool_calls': 'tool_use', 'length': 'max_tokens'}.get(choice.get('finish_reason'), 'end_turn'),
            'stop_sequence': None, 'usage': {'input_tokens': usage.get('prompt_tokens', 0),
                                           'output_tokens': usage.get('completion_tokens', 0)}}


def message_events(message):
    def event(kind, **data):
        return ('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **data}) + '\n\n').encode()
    yield event('message_start', message={**message, 'content': [], 'stop_reason': None,
                                         'usage': {**message['usage'], 'output_tokens': 0}})
    for index, block in enumerate(message['content']):
        tool = block['type'] == 'tool_use'
        yield event('content_block_start', index=index, content_block={**block, **({'input': {}} if tool else {'text': ''})})
        delta = {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])} if tool else {'type': 'text_delta', 'text': block['text']}
        yield event('content_block_delta', index=index, delta=delta)
        yield event('content_block_stop', index=index)
    yield event('message_delta', delta={'stop_reason': message['stop_reason'], 'stop_sequence': None},
                usage={'output_tokens': message['usage']['output_tokens']})
    yield event('message_stop')
