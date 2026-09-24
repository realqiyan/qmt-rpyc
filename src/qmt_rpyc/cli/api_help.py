"""Readable offline API help and an explicit JSON documentation view."""
import json
import textwrap

from qmt_rpyc.contracts.common import OperationError
from qmt_rpyc.contracts.documentation import OPERATION_DESCRIPTIONS, field_description
from qmt_rpyc.contracts.operations import OPERATIONS
from qmt_rpyc.contracts.schema import schema


def documented_schema(node):
    if 'model' in node:
        return {**node, 'fields': {
            name: {**field, 'type': documented_schema(field['type']),
                   'description': field_description(node['model'], name)}
            for name, field in node['fields'].items()}}
    if 'args' in node:
        return {**node, 'args': [documented_schema(arg) if isinstance(arg, dict) else arg
                                for arg in node['args']]}
    return node


def list_apis(group=None):
    if group and group not in {name.split('.')[0] for name in OPERATIONS}:
        raise ValueError('unknown API group: ' + group)
    return {name: {'description': OPERATION_DESCRIPTIONS[name]}
            for name in sorted(OPERATIONS) if not group or name.startswith(group + '.')}


def describe_api(name):
    if name not in OPERATIONS:
        raise ValueError('unknown operation: ' + name)
    operation = OPERATIONS[name]
    return {
        'name': name, 'description': OPERATION_DESCRIPTIONS[name],
        'mutation': operation.mutation,
        'request': documented_schema(schema(operation.request_type)),
        'response': documented_schema(schema(operation.response_type)),
        'error': documented_schema(schema(OperationError)),
    }


def type_label(node):
    if not isinstance(node, dict):
        return repr(node) if isinstance(node, str) and node != '...' else str(node)
    if 'model' in node:
        return node['model']
    name = node.get('type', node.get('parameter', '?'))
    if name == 'NoneType':
        return 'null'
    if name == 'datetime':
        return 'datetime (ISO 8601，含时区)'
    if name == 'date':
        return 'date (YYYY-MM-DD)'
    args = node.get('args', [])
    if name == 'Union':
        return ' | '.join(type_label(arg) for arg in args)
    if name == 'Literal':
        return ' | '.join(repr(arg) for arg in args)
    return name + ('[' + ', '.join(type_label(arg) for arg in args) + ']' if args else '')


def _models(node):
    if not isinstance(node, dict):
        return
    if 'model' in node:
        yield node
        for field in node['fields'].values():
            yield from _models(field['type'])
    for arg in node.get('args', []):
        yield from _models(arg)


def render_list(apis):
    return '\n'.join(name + '  ' + item['description'] for name, item in apis.items())


def render_description(api):
    lines = [api['name'], api['description'],
             '调用性质：' + ('写操作；结果不确定时不可重试。' if api['mutation'] else '查询。'),
             '类型约定：tuple 在 JSON 中为数组；null 与空数组不同；未知字段拒绝。']
    for key, title in [('request', '请求参数'), ('response', '返回结果'), ('error', '操作失败')]:
        node = api[key]
        lines.extend(['', title + ': ' + type_label(node)])
        seen = set()
        for model in _models(node):
            if model['model'] in seen:
                continue
            seen.add(model['model'])
            lines.append('  ' + model['model'])
            if not model['fields']:
                lines.append('    无字段（{}）')
            for name, field in model['fields'].items():
                requirement = ''
                if key == 'request':
                    requirement = ('；可省略，默认 ' + json.dumps(field['default'], ensure_ascii=False)
                                   if 'default' in field else '；必填')
                label = name + ': ' + type_label(field['type']) + requirement
                lines.extend(textwrap.wrap(label, width=100, initial_indent='    ', subsequent_indent='      ', break_long_words=False))
                lines.extend(textwrap.wrap(field['description'], width=46, initial_indent='      ', subsequent_indent='      '))
    lines.extend(['', 'JSON 详情：qmt-rpyc-client api describe ' + api['name'] + ' --json'])
    return '\n'.join(lines)
